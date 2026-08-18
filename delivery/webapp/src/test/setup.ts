import "@testing-library/jest-dom";

// This Node build exposes an experimental global `localStorage` that requires
// `--localstorage-file`, and jsdom's own storage ends up undefined. Provide a
// minimal in-memory Storage so client code that reads/writes localStorage
// (e.g. the Completion gate) is testable in jsdom.
if (
  typeof window !== "undefined" &&
  typeof window.localStorage === "undefined"
) {
  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key) {
      return store.has(key) ? store.get(key)! : null;
    },
    key(index) {
      return [...store.keys()][index] ?? null;
    },
    removeItem(key) {
      store.delete(key);
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
  };
  Object.defineProperty(window, "localStorage", {
    value: storage,
    configurable: true,
  });
}

