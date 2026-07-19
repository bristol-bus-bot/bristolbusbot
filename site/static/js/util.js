/** Create a DOM element using the supported attributes and event handlers. */
export function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class") node.className = String(value);
        else if (key === "id") node.id = String(value);
        else if (key.startsWith("data-")) node.setAttribute(key, String(value));
        else if (key.startsWith("aria-") || key === "role" || key === "title")
            node.setAttribute(key, String(value));
        else if (key === "tabindex")
            node.setAttribute("tabindex", String(value));
        else if (key === "href" && tag === "a") {
            const url = String(value);
            if (/^\s*(javascript|data):/i.test(url))
                throw new Error(`Refused unsafe href: ${url}`);
            node.setAttribute("href", url);
        }
        else if (key === "target" && tag === "a") {
            node.setAttribute("target", String(value));
            // Prevent opener access from new-tab links.
            if (String(value) === "_blank") node.relList.add("noopener");
        }
        else if (key === "rel" && tag === "a")
            String(value).split(/\s+/).forEach(t => t && node.relList.add(t));
        else if (["src", "alt", "width", "height"].includes(key) && tag === "img")
            node.setAttribute(key, String(value));
        else if (["type", "value", "placeholder", "name"].includes(key)
                 && ["input", "textarea", "select"].includes(tag))
            node.setAttribute(key, String(value));
        else if (key === "for" && tag === "label")
            node.setAttribute("for", String(value));
        else if (key === "onClick") node.addEventListener("click", value);
        else if (key === "onChange") node.addEventListener("change", value);
        else if (key === "onInput") node.addEventListener("input", value);
        else if (key === "onSubmit") node.addEventListener("submit", value);
        else if (key === "onKeyDown") node.addEventListener("keydown", value);
        else throw new Error(`Unsafe or unsupported attribute: ${key} on <${tag}>`);
    }
    for (const child of children.flat()) {
        if (child === null || child === undefined || child === false) continue;
        node.appendChild(
            typeof child === "string" || typeof child === "number"
                ? document.createTextNode(String(child)) : child);
    }
    return node;
}

/** Replace a target's contents with one child tree. */
export function replaceContent(target, newChild) {
    target.replaceChildren();
    if (newChild) target.appendChild(newChild);
}

/** Replace contents with several children. */
export function replaceContents(target, children) {
    target.replaceChildren(...children.filter(Boolean));
}
