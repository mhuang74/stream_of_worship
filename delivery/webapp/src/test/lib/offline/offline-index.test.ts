import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from "vitest";
import {
  OFFLINE_INDEX_DB_NAME,
  OFFLINE_INDEX_STORE_NAME,
  getOfflineRecord,
  listOfflineRecords,
  putOfflineRecord,
  removeOfflineSongset,
  type OfflineSongsetRecord,
} from "@/lib/offline/offline-index";
import { invalidateArtifactCache } from "@/lib/offline/artifact-cache";

vi.mock("@/lib/offline/artifact-cache", () => ({
  invalidateArtifactCache: vi.fn().mockResolvedValue(undefined),
}));

// The index's eviction paths also delete the pre-cached controller document
// (issue #210) — mocked here, asserted per eviction below.
const mockDeleteControllerDocument = vi.hoisted(() =>
  vi.fn<(songsetId: string) => Promise<boolean>>().mockResolvedValue(true)
);
vi.mock("@/lib/offline/document-cache", () => ({
  deleteControllerDocument: mockDeleteControllerDocument,
}));

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

interface FakeOpenRequest {
  onupgradeneeded: ((event: unknown) => void) | null;
  onsuccess: ((event: unknown) => void) | null;
  onerror: ((event: unknown) => void) | null;
  result: unknown;
  error: unknown;
}

interface IndexDbHarness {
  fakeIndexedDb: { open: Mock };
  store: Map<string, OfflineSongsetRecord>;
}

function makeIndexDbMock(seed?: Map<string, OfflineSongsetRecord>): IndexDbHarness {
  const store = seed ?? new Map<string, OfflineSongsetRecord>();

  const makeObjectStore = (fakeStore: Map<string, OfflineSongsetRecord>) => ({
    get: (key: string) => {
      const req = { result: fakeStore.get(key) } as unknown as FakeOpenRequest;
      queueMicrotask(() => req.onsuccess?.call(req, { target: req }));
      return req;
    },
    put: (value: OfflineSongsetRecord) => {
      fakeStore.set(value.songsetId, value);
      const req = { result: undefined } as unknown as FakeOpenRequest;
      queueMicrotask(() => req.onsuccess?.call(req, { target: req }));
      return req;
    },
    getAll: () => {
      const req = { result: [...fakeStore.values()] } as unknown as FakeOpenRequest;
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
      contains: (name: string) => name === OFFLINE_INDEX_STORE_NAME,
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
        onupgradeneeded: null,
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

function makeFailingOpenMock(): IndexDbHarness["fakeIndexedDb"] {
  return {
    open: vi.fn(() => {
      const req: FakeOpenRequest = {
        onupgradeneeded: null,
        onsuccess: null,
        onerror: null,
        result: undefined,
        error: new Error("storage unavailable"),
      };
      queueMicrotask(() => req.onerror?.call(req, { target: req }));
      return req;
    }),
  };
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

function makeRecord(overrides: Partial<OfflineSongsetRecord> = {}): OfflineSongsetRecord {
  return {
    songsetId: "set-1",
    renderJobId: "job-1",
    songsetName: "Sunday Worship",
    cachedMp3: true,
    cachedMp4: true,
    cachedChapters: true,
    cachedAt: "2026-09-15T10:00:00.000Z",
    chapterContentHashes: ["hash-a", "hash-b"],
    ...overrides,
  };
}

const mockedInvalidate = vi.mocked(invalidateArtifactCache);

// --------------------------------------------------------------------------
// Constants
// --------------------------------------------------------------------------

describe("constants", () => {
  it("OFFLINE_INDEX_DB_NAME is sow-offline-index", () => {
    expect(OFFLINE_INDEX_DB_NAME).toBe("sow-offline-index");
  });

  it("OFFLINE_INDEX_STORE_NAME is songsets", () => {
    expect(OFFLINE_INDEX_STORE_NAME).toBe("songsets");
  });
});

// --------------------------------------------------------------------------
// put → get → list → delete round-trip
// --------------------------------------------------------------------------

describe("index round-trip", () => {
  let mock: IndexDbHarness;

  beforeEach(() => {
    mock = makeIndexDbMock();
    setIndexedDb(mock.fakeIndexedDb);
  });

  afterEach(() => {
    deleteIndexedDb();
    vi.restoreAllMocks();
  });

  it("put writes a readable record", async () => {
    const record = makeRecord();

    await putOfflineRecord(record);

    expect(await getOfflineRecord("set-1")).toEqual(record);
  });

  it("get returns null for an unknown songset", async () => {
    expect(await getOfflineRecord("missing")).toBeNull();
  });

  it("list returns every record", async () => {
    await putOfflineRecord(makeRecord({ songsetId: "set-1" }));
    await putOfflineRecord(
      makeRecord({ songsetId: "set-2", renderJobId: "job-2", songsetName: "Evening" })
    );

    const records = await listOfflineRecords();
    expect(records).toHaveLength(2);
    expect(records.map((r) => r.songsetId).sort()).toEqual(["set-1", "set-2"]);
  });

  it("list returns empty array when the index is empty", async () => {
    expect(await listOfflineRecords()).toEqual([]);
  });

  it("remove deletes the record and invalidates its artifacts", async () => {
    await putOfflineRecord(makeRecord());
    mockedInvalidate.mockClear();

    await removeOfflineSongset("set-1");

    expect(await getOfflineRecord("set-1")).toBeNull();
    expect(mockedInvalidate).toHaveBeenCalledWith("job-1");
    // Issue #210: the pre-cached controller document goes with it.
    expect(mockDeleteControllerDocument).toHaveBeenCalledWith("set-1");
  });

  it("put overwrites a prior record for the same songsetId", async () => {
    await putOfflineRecord(makeRecord({ songsetName: "Old name" }));
    await putOfflineRecord(makeRecord({ songsetName: "New name" }));

    const loaded = await getOfflineRecord("set-1");
    expect(loaded?.songsetName).toBe("New name");
    expect(await listOfflineRecords()).toHaveLength(1);
  });
});

// --------------------------------------------------------------------------
// Supersede eviction
// --------------------------------------------------------------------------

describe("putOfflineRecord supersede eviction", () => {
  beforeEach(() => {
    setIndexedDb(makeIndexDbMock().fakeIndexedDb);
  });

  afterEach(() => {
    deleteIndexedDb();
    vi.restoreAllMocks();
  });

  it("deletes the prior renderJobId's artifacts before writing a new record", async () => {
    await putOfflineRecord(makeRecord({ songsetId: "set-1", renderJobId: "job-old" }));
    mockedInvalidate.mockClear();

    await putOfflineRecord(makeRecord({ songsetId: "set-1", renderJobId: "job-new" }));

    expect(mockedInvalidate).toHaveBeenCalledTimes(1);
    expect(mockedInvalidate).toHaveBeenCalledWith("job-old");

    const loaded = await getOfflineRecord("set-1");
    expect(loaded?.renderJobId).toBe("job-new");
  });

  // Issue #210: a re-download supersedes the old copy — its pre-cached
  // controller document (stale RSC hashes, possibly a different page) must
  // go with the old artifacts.
  it("deletes the prior controller document when superseding", async () => {
    await putOfflineRecord(makeRecord({ songsetId: "set-1", renderJobId: "job-old" }));
    mockDeleteControllerDocument.mockClear();

    await putOfflineRecord(makeRecord({ songsetId: "set-1", renderJobId: "job-new" }));

    expect(mockDeleteControllerDocument).toHaveBeenCalledTimes(1);
    expect(mockDeleteControllerDocument).toHaveBeenCalledWith("set-1");
  });

  it("does not evict when renderJobId is unchanged", async () => {
    await putOfflineRecord(makeRecord({ songsetId: "set-1", renderJobId: "job-1" }));
    mockedInvalidate.mockClear();
    mockDeleteControllerDocument.mockClear();

    await putOfflineRecord(
      makeRecord({ songsetId: "set-1", renderJobId: "job-1", songsetName: "Renamed" })
    );

    expect(mockedInvalidate).not.toHaveBeenCalled();
    expect(mockDeleteControllerDocument).not.toHaveBeenCalled();
  });

  it("does not evict when no prior record exists", async () => {
    await putOfflineRecord(makeRecord({ songsetId: "fresh", renderJobId: "job-1" }));

    expect(mockedInvalidate).not.toHaveBeenCalled();
    expect(mockDeleteControllerDocument).not.toHaveBeenCalled();
  });
});

// --------------------------------------------------------------------------
// removeOfflineSongset artifact invalidation
// --------------------------------------------------------------------------

describe("removeOfflineSongset artifact invalidation", () => {
  beforeEach(() => {
    setIndexedDb(makeIndexDbMock().fakeIndexedDb);
  });

  afterEach(() => {
    deleteIndexedDb();
    vi.restoreAllMocks();
  });

  it("does not invalidate anything for an unknown songset", async () => {
    await removeOfflineSongset("missing");

    expect(mockedInvalidate).not.toHaveBeenCalled();
    expect(mockDeleteControllerDocument).not.toHaveBeenCalled();
  });
});

// --------------------------------------------------------------------------
// Graceful degradation — no-throw when IndexedDB is unavailable
// --------------------------------------------------------------------------

describe("graceful degradation", () => {
  afterEach(() => {
    deleteIndexedDb();
    vi.restoreAllMocks();
  });

  it("get returns null when IndexedDB is unavailable", async () => {
    deleteIndexedDb();
    expect(await getOfflineRecord("set-1")).toBeNull();
  });

  it("list returns empty array when IndexedDB is unavailable", async () => {
    deleteIndexedDb();
    expect(await listOfflineRecords()).toEqual([]);
  });

  it("put resolves without throwing when IndexedDB is unavailable", async () => {
    deleteIndexedDb();
    await expect(putOfflineRecord(makeRecord())).resolves.toBeUndefined();
  });

  it("remove resolves without throwing when IndexedDB is unavailable", async () => {
    deleteIndexedDb();
    await expect(removeOfflineSongset("set-1")).resolves.toBeUndefined();
  });

  it("get returns null and list returns empty when open fails", async () => {
    setIndexedDb(makeFailingOpenMock());

    expect(await getOfflineRecord("set-1")).toBeNull();
    expect(await listOfflineRecords()).toEqual([]);
  });

  it("put resolves without throwing when open fails", async () => {
    setIndexedDb(makeFailingOpenMock());

    await expect(putOfflineRecord(makeRecord())).resolves.toBeUndefined();
  });
});
