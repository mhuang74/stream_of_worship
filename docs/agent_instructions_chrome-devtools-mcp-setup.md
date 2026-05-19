# Opening Stream of Worship Webapp with Chrome DevTools MCP

## Prerequisites

- Google Chrome installed at `/Applications/Google Chrome.app`
- `chrome-devtools-mcp` configured in your MCP setup (connects to `http://127.0.0.1:9222`)

## Step 1: Kill Existing Chrome Processes

If Chrome is already running without remote debugging, kill it first:

```bash
pkill -9 -f "Google Chrome" 2>/dev/null
sleep 3
```

**Important:** Chrome cannot enable remote debugging on an already-running instance. It must be launched fresh with the `--remote-debugging-port` flag.

## Step 2: Launch Chrome with Remote Debugging

```bash
rm -rf /tmp/chrome-debug-profile
mkdir -p /tmp/chrome-debug-profile
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=TranslateUI \
  about:blank > /tmp/chrome-debug.log 2>&1 &
```

Key flags:
- `--remote-debugging-port=9222` — Enables DevTools Protocol on port 9222
- `--user-data-dir=/tmp/chrome-debug-profile` — **Required**. Chrome refuses remote debugging without a non-default data directory
- `--no-first-run` — Skips first-run dialogs
- `--no-default-browser-check` — Skips default browser prompt

## Step 3: Verify Chrome DevTools is Accessible

```bash
sleep 5
curl -s http://127.0.0.1:9222/json/version
```

Expected response (JSON with browser info):
```json
{
   "Browser": "Chrome/148.0.xxx",
   "Protocol-Version": "1.3",
   "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/..."
}
```

If this returns empty, Chrome likely crashed during startup. Check `/tmp/chrome-debug.log` for errors.

## Step 4: Connect via Chrome DevTools MCP

Use the MCP tools to interact with the browser:

1. **List pages:** `chrome-devtools_list_pages` — confirms connection is working
2. **Navigate to webapp:** `chrome-devtools_navigate_page` with `type=url`, `url=http://localhost:8080`
3. **Take snapshot:** `chrome-devtools_take_snapshot` — get text content of the page (a11y tree)
4. **Take screenshot:** `chrome-devtools_take_screenshot` — capture visual screenshot (note: some models cannot read images)

## Troubleshooting

### Chrome crashes on launch (GPU process exit_code=15)
This is a known issue on macOS. The `--headless=new` mode also crashes. Use the non-headless launch command above — despite the GPU error messages in the log, Chrome typically still starts and binds to port 9222 successfully.

### Port 9222 not binding
- Ensure no other process is using port 9222: `lsof -i :9222`
- Ensure `--user-data-dir` is specified (Chrome silently ignores `--remote-debugging-port` without it)
- Try with a fresh profile: `rm -rf /tmp/chrome-debug-profile`

### "Could not connect to Chrome" from MCP
- Verify Chrome is running: `ps aux | grep Chrome`
- Verify port is bound: `lsof -i :9222`
- Verify DevTools endpoint: `curl http://127.0.0.1:9222/json/version`
- If all fail, restart Chrome from Step 1

## Quick Reference (Copy-Paste)

```bash
# Full launch sequence
pkill -9 -f "Google Chrome" 2>/dev/null; sleep 3
rm -rf /tmp/chrome-debug-profile && mkdir -p /tmp/chrome-debug-profile
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-debug-profile \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=TranslateUI \
  about:blank > /tmp/chrome-debug.log 2>&1 &
sleep 5
curl -s http://127.0.0.1:9222/json/version
```

Then use MCP tool: `chrome-devtools_navigate_page` → `http://localhost:8080`
