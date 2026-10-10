import { bundle } from "../messages";

// Favorites page UI chrome (issue #143).
// Translations are Traditional Chinese (繁體中文). Song metadata, lyrics, and
// user-entered content remain verbatim. The count description interpolates a
// completion percentage via a `${percent}` token replaced at the call site.

export const favoritesBundle = bundle({
  en: {
    "favorites.title": "Favorites",
    "favorites.count.singular": "favorite song",
    "favorites.count.plural": "favorite songs",
    "favorites.empty.title": "No favorites yet",
    "favorites.empty.description":
      "Listen to at least ${percent}% of a song in the songset builder, then tap the heart to favorite it. Your favorites are pinned to the top.",
    "favorites.empty.action": "Go to Songsets",
    "favorites.loadFailed": "Failed to load favorites",
    "favorites.pagination.ariaLabel": "Favorites pagination",
    "favorites.pagination.previous": "Previous page",
    "favorites.pagination.next": "Next page",
    "favorites.pagination.page": "Page ${n}",
    "favorites.pagination.prevLabel": "Prev",
    "favorites.pagination.nextLabel": "Next",

    // Select mode (issue #253): pick favorites across pages and create a songset.
    "favorites.select.enter": "Select",
    "favorites.select.exit": "Cancel",
    "favorites.select.selectedCount": "${n} selected",
    "favorites.select.createCta": "Create songset (${n})",
    "favorites.select.createDialogDescription":
      "Creating a songset with ${n} favorite song(s) in the order selected.",
  },
  "zh-Hant": {
    "favorites.title": "我的最愛",
    "favorites.count.singular": "首最愛詩歌",
    "favorites.count.plural": "首最愛詩歌",
    "favorites.empty.title": "還沒有最愛",
    "favorites.empty.description":
      "在敬拜歌單編輯器裡聽完一首歌至少 ${percent}%，再點愛心加入最愛。你的最愛會固定排在最上面。",
    "favorites.empty.action": "前往敬拜歌單",
    "favorites.loadFailed": "最愛載入失敗，請再試一次",
    "favorites.pagination.ariaLabel": "我的最愛分頁",
    "favorites.pagination.previous": "上一頁",
    "favorites.pagination.next": "下一頁",
    "favorites.pagination.page": "第 ${n} 頁",
    "favorites.pagination.prevLabel": "上一頁",
    "favorites.pagination.nextLabel": "下一頁",

    // Select mode (issue #253): pick favorites across pages and create a songset.
    "favorites.select.enter": "選取",
    "favorites.select.exit": "取消",
    "favorites.select.selectedCount": "已選取 ${n} 首",
    "favorites.select.createCta": "建立敬拜歌單（${n}）",
    "favorites.select.createDialogDescription":
      "將以所選順序建立包含 ${n} 首最愛詩歌的敬拜歌單。",
  },
});
