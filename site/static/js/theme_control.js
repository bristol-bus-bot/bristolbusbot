(function installThemeControl() {
    const key = "bbb-theme";

    function storedTheme() {
        try {
            const value = window.localStorage.getItem(key);
            return value === "night" || value === "day" ? value : null;
        } catch (_error) {
            return null;
        }
    }

    function updateControls(theme) {
        document.querySelectorAll("[data-theme-toggle]").forEach(toggle => {
            const night = theme === "night";
            toggle.setAttribute("aria-pressed", String(night));
            toggle.title = night ? "Switch to day mode" : "Switch to night mode";
            toggle.setAttribute(
                "aria-label",
                night ? "Switch to day mode" : "Switch to night mode",
            );
        });
        const themeColor = document.querySelector('meta[name="theme-color"]');
        if (themeColor)
            themeColor.content = theme === "night" ? "#171b21" : "#eef0f2";
    }

    function applyTheme(theme, { persist = true } = {}) {
        const next = theme === "night" ? "night" : "day";
        document.documentElement.dataset.theme = next;
        if (persist) {
            try {
                window.localStorage.setItem(key, next);
            } catch (_error) {
                // The choice still applies for the current page view.
            }
        }
        updateControls(next);
        document.dispatchEvent(new CustomEvent("bbb:themechange", {
            detail: { theme: next },
        }));
    }

    document.addEventListener("DOMContentLoaded", () => {
        updateControls(document.documentElement.dataset.theme === "night"
            ? "night" : "day");
        document.querySelectorAll("[data-theme-toggle]").forEach(toggle => {
            toggle.addEventListener("click", () => {
                applyTheme(document.documentElement.dataset.theme === "night"
                    ? "day" : "night");
            });
        });
        const preference = window.matchMedia?.("(prefers-color-scheme: dark)");
        preference?.addEventListener?.("change", event => {
            if (!storedTheme())
                applyTheme(event.matches ? "night" : "day", { persist: false });
        });
    });
})();
