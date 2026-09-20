import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import {
  getShareOfflineRecord,
  putShareOfflineRecord,
  removeShareOfflineRecord,
  SHARE_OFFLINE_INDEX_DB_NAME,
  SHARE_OFFLINE_INDEX_STORE_NAME,
  type OfflineShareRecord,
} from "@/lib/offline/share-offline-index";
import { invalidateArtifactCache } from "@/lib/offline/artifact-cache";

vi.mock("@/lib/offline/artifact-cache", () => ({
  invalidateArtifactCache: vi.fn().mockResolvedValue(undefined),
}));

// The share index's eviction paths also delete the pre-cached share
// controller document (issue #218 PR2) — mocked here, asserted per eviction.
const mockDeleteShareControllerDocument = vi.hoisted(() =>
  vi.fn<(token: string) => Promise<boolean>>().mockResolvedValue(true)
);
vi.mock("@/lib/offline/document-cache", () => ({
  deleteShareControllerDocument: mockDeleteShareControllerDocument,
}));

// Same fake-IndexedDB harness shape as the owner index test — the share
// index is a second DB with a token keyPath.
interface FakeOpenRequest {
  onupgradeneeded: ((event: { target: unknown }) => void) | null;
  onsuccess: ((event: { target: unknown }) => void) | null;
  onerror: ((event: { target: unknown }) => void) | null;
  result: unknown;
  error: unknown;
}

interface IndexDbHarness {
  fakeIndexedDb: { open: Mock };
  store: Map<string, OfflineShareRecord>;
}

function makeIndexDbMock(seed?: Map<string, OfflineShareRecord>): IndexDbHarness {
  const store = seed ?? new Map<string, OfflineShareRecord>();

  const makeObjectStore = (fakeStore: Map<string, OfflineShareRecord>) => ({
    get: (key: string) => {
      const req = { result: fakeStore.get(key) } as unknown as FakeOpenRequest;
      queueMicrotask(() => req.onsuccess?.call(req, { target: req }));
      return req;
    },
    put: (value: OfflineShareRecord) => {
      fakeStore.set(value.token, value);
      const req = { result: undefined } as unknown as FakeOpenRequest;
      queueMicrotask(() => req.onsuccess?.call(req, { target: req }));
      return req;
    },
    delete: (key: string) => {
      fakeStore.delete(key);
      const req = { result: undefined } as unknown as FakeOpenRequest;
      queueMicrotask(() => req.onsuccess?.call(req, { target: req }));
      return req;
    },
  });

  const fakeDb = {
    close: vi.fn(),
    objectStoreNames: {
      contains: (name: string) => name === SHARE_OFFLINE_INDEX_STORE_NAME,
    },
    createObjectStore: vi.fn(),
    transaction: () => {
      const objectStore = makeObjectStore(store);
      return {
        objectStore: () => objectStore,
        onabort: null,
        onerror: null,
        oncomplete: null,
      };
    },
  };

  const fakeIndexedDb = {
    open: vi.fn(() => {
      const req: FakeOpenRequest = {
        onupgradeneeded: (event) => {
          const db = (event.target as { result: IDBDatabase }).result;
          if (!db.objectStoreNames.contains(SHARE_OFFLINE_INDEX_STORE_NAME)) {
            db.createObjectStore(SHARE_OFFLINE_INDEX_STORE_NAME, { keyPath: "token" });
          }
        },
        onsuccess: null,
        onerror: null,
        result: undefined,
        error: undefined,
      };
      queueMicrotask(() => {
        req.result = fakeDb;
        req.onsuccess?.call(req, { target: req });
      });
      return req;
    }),
  };

  return { fakeIndexedDb, store };
}

function setIndexedDb(fakeIndexedDb: unknown) {
  Object.defineProperty(global, "indexedDB", {
    value: fakeIndexedDb,
    configurable: true,
    writable: true,
  });
}

function deleteIndexedDb() {
  Reflect.deleteProperty(global, "indexedDB");
}

function makeRecord(overrides: Partial<OfflineShareRecord> = {}): OfflineShareRecord {
  return {
    token: "tok-1",
    renderJobId: "job-1",
    songsetName: "Shared Set",
    cachedMp3: true,
    cachedMp4: true,
    cachedChapters: true,
    cachedAt: "2026-09-20T10:00:00.000Z",
    chapterContentHashes: ["hash-a"],
    ...overrides,
  };
}

const mockedInvalidate = vi.mocked(invalidateArtifactCache);

describe("share offline index constants", () => {
  it("uses its own database name — never the owner index DB", () => {
    expect(SHARE_OFFLINE_INDEX_DB_NAME).toBe("sow-share-offline-index");
    expect(SHARE_OFFLINE_INDEX_DB_NAME).not.toBe("sow-offline-index");
  });

  it("uses the shares store", () => {
    expect(SHARE_OFFLINE_INDEX_STORE_NAME).toBe("shares");
  });
});

describe("share index round-trip", () => {
  let mock: IndexDbHarness;

  beforeEach(() => {
    mock = makeIndexDbMock();
    setIndexedDb(mock.fakeIndexedDb);
  });

  afterEach(() => {
    deleteIndexedDb();
    vi.restoreAllMocks();
  });

  it("put writes a readable record keyed by token", async () => {
    await putShareOfflineRecord(makeRecord());
    expect(await getShareOfflineRecord("tok-1")).toEqual(makeRecord());
  });

  it("get returns null for an unknown token", async () => {
    expect(await getShareOfflineRecord("missing")).toBeNull();
  });

  it("put overwrites a prior record for the same token", async () => {
    await putShareOfflineRecord(makeRecord({ songsetName: "Old" }));
    await putShareOfflineRecord(makeRecord({ songsetName: "New" }));

    const loaded = await getShareOfflineRecord("tok-1");
    expect(loaded?.songsetName).toBe("New");
  });

  // ADR-0009: the copy is a frozen snapshot — but a Re-download supersedes
  // it, and then the superseded render's artifact entries must go.
  it("supersede evicts the prior renderJobId's artifacts and document", async () => {
    await putShareOfflineRecord(makeRecord({ token: "tok-1", renderJobId: "job-old" }));
    mockedInvalidate.mockClear();
    mockDeleteShareControllerDocument.mockClear();

    await putShareOfflineRecord(makeRecord({ token: "tok-1", renderJobId: "job-new" }));

    expect(mockedInvalidate).toHaveBeenCalledWith("job-old");
    expect(mockDeleteShareControllerDocument).toHaveBeenCalledWith("tok-1");
    expect((await getShareOfflineRecord("tok-1"))?.renderJobId).toBe("job-new");
  });

  it("does not evict when renderJobId is unchanged", async () => {
    await putShareOfflineRecord(makeRecord());
    mockedInvalidate.mockClear();
    mockDeleteShareControllerDocument.mockClear();

    await putShareOfflineRecord(makeRecord({ songsetName: "Renamed" }));

    expect(mockedInvalidate).not.toHaveBeenCalled();
    expect(mockDeleteShareControllerDocument).not.toHaveBeenCalled();
  });

  it("remove deletes the record and invalidates its artifacts", async () => {
    await putShareOfflineRecord(makeRecord());
    mockedInvalidate.mockClear();

    await removeShareOfflineRecord("tok-1");

    expect(await getShareOfflineRecord("tok-1")).toBeNull();
    expect(mockedInvalidate).toHaveBeenCalledWith("job-1");
    expect(mockDeleteShareControllerDocument).toHaveBeenCalledWith("tok-1");
  });
});

describe("share index graceful degradation", () => {
  afterEach(() => {
    deleteIndexedDb();
    vi.restoreAllMocks();
  });

  it("get returns null when IndexedDB is unavailable", async () => {
    deleteIndexedDb();
    expect(await getShareOfflineRecord("tok-1")).toBeNull();
  });

  it("put resolves without throwing when IndexedDB is unavailable", async () => {
    deleteIndexedDb();
    await expect(putShareOfflineRecord(makeRecord())).resolves.toBeUndefined();
  });

  it("remove resolves without throwing when IndexedDB is unavailable", async () => {
    deleteIndexedDb();
    await expect(removeShareOfflineRecord("tok-1")).resolves.toBeUndefined();
  });
});
