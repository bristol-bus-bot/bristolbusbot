(function bootstrapTheme() {
    const key = "bbb-theme";
    let stored = null;
    try {
        stored = window.localStorage.getItem(key);
    } catch (_error) {
        // Storage can be unavailable in strict privacy modes.
    }
    const preferred = window.matchMedia?.("(prefers-color-scheme: dark)").matches
        ? "night" : "day";
    const theme = stored === "night" || stored === "day" ? stored : preferred;
    document.documentElement.dataset.theme = theme;
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) {
        themeColor.content = theme === "night" ? "#171b21" : "#eef0f2";
    }
})();
