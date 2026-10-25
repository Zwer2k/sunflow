import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";

const MAX_RESULTS = 50;

/**
 * Searchable single choice (combobox) for long lists such as entities.
 *
 * options: [{value, label, detail?}]; fires "change" with detail {value}.
 * An empty value is allowed when emptyLabel is set (e.g. "keine").
 */
class SunflowPicker extends LitElement {
  static properties = {
    options: { attribute: false },
    value: {},
    label: {},
    placeholder: {},
    emptyLabel: { attribute: "empty-label" },
    // Inside scrolling containers (tables) the list takes space instead of floating
    inline: { type: Boolean, reflect: true },
    _open: { state: true },
    _query: { state: true },
    _active: { state: true },
  };

  constructor() {
    super();
    this.options = [];
    this.value = "";
    this.label = "";
    this.placeholder = "Suchen …";
    this.emptyLabel = null;
    this.inline = false;
    this._open = false;
    this._query = "";
    this._active = 0;
    this._listId = `sf-picker-${Math.random().toString(36).slice(2)}`;
  }

  get _matches() {
    const words = this._query.toLowerCase().split(/\s+/).filter(Boolean);
    const all = this.emptyLabel !== null ? [{ value: "", label: this.emptyLabel }, ...this.options] : this.options;
    const found = all.filter((o) => {
      const text = `${o.label} ${o.value} ${o.detail ?? ""}`.toLowerCase();
      return words.every((w) => text.includes(w));
    });
    return found.slice(0, MAX_RESULTS);
  }

  get _selected() {
    if (!this.value) return this.emptyLabel ?? "";
    const option = this.options.find((o) => o.value === this.value);
    return option ? option.label : this.value;
  }

  _openList() {
    this._open = true;
    this._query = "";
    this._active = 0;
  }

  _choose(option) {
    this.value = option.value;
    this._open = false;
    this._query = "";
    this.dispatchEvent(new CustomEvent("change", { detail: { value: option.value }, bubbles: true, composed: true }));
  }

  _onKey(event) {
    const matches = this._matches;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        if (!this._open) this._openList();
        else this._active = Math.min(this._active + 1, matches.length - 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        this._active = Math.max(this._active - 1, 0);
        break;
      case "Enter":
        if (this._open && matches[this._active]) {
          event.preventDefault();
          this._choose(matches[this._active]);
        }
        break;
      case "Escape":
        this._open = false;
        this._query = "";
        break;
      default:
        break;
    }
  }

  updated() {
    if (this._open) {
      this.renderRoot.querySelector(`#${this._listId}-${this._active}`)?.scrollIntoView({ block: "nearest" });
    }
  }

  render() {
    const matches = this._open ? this._matches : [];
    const total = this._open ? (this.emptyLabel !== null ? 1 : 0) + this.options.length : 0;
    return html`
      <div class="box">
        <input
          role="combobox"
          aria-label=${this.label}
          aria-expanded=${this._open}
          aria-controls=${this._listId}
          aria-activedescendant=${this._open && matches.length ? `${this._listId}-${this._active}` : ""}
          aria-autocomplete="list"
          placeholder=${this._open ? this.placeholder : ""}
          .value=${this._open ? this._query : this._selected}
          @focus=${() => this._openList()}
          @click=${() => !this._open && this._openList()}
          @input=${(e) => {
            this._query = e.target.value;
            this._active = 0;
            this._open = true;
          }}
          @keydown=${(e) => this._onKey(e)}
          @blur=${() => setTimeout(() => (this._open = false), 150)}
        >
        <span class="chevron" aria-hidden="true">▾</span>
      </div>
      ${this._open
        ? html`
            <ul class="list" role="listbox" id=${this._listId} aria-label=${this.label}>
              ${matches.length
                ? matches.map(
                    (o, i) => html`
                      <li
                        role="option"
                        id="${this._listId}-${i}"
                        aria-selected=${o.value === this.value}
                        class=${i === this._active ? "active" : ""}
                        @mousedown=${(e) => {
                          // Keep focus in the input so blur does not close before the choice
                          e.preventDefault();
                          this._choose(o);
                        }}
                        @mousemove=${() => (this._active = i)}
                      >
                        <span class="label">${o.label}</span>
                        ${o.value && o.value !== o.label ? html`<span class="detail">${o.value}${o.detail ? ` · ${o.detail}` : ""}</span>` : nothing}
                      </li>
                    `,
                  )
                : html`<li class="empty">Keine Treffer</li>`}
              ${total > MAX_RESULTS && matches.length === MAX_RESULTS
                ? html`<li class="empty">Weitere Treffer – Suche verfeinern</li>`
                : nothing}
            </ul>
          `
        : nothing}
    `;
  }

  static styles = css`
    :host { display: block; position: relative; min-width: 0; }
    .box { position: relative; }
    input {
      width: 100%; box-sizing: border-box; min-height: 44px; padding: 0 32px 0 12px; font: inherit; font-size: 14px;
      border: 1px solid var(--sf-border-strong); border-radius: 8px; background: var(--sf-surface); color: var(--sf-text);
      text-overflow: ellipsis;
    }
    input:focus { outline: 2px solid var(--sf-primary); outline-offset: 0; }
    :host([inline]) .list { position: static; margin-top: 4px; box-shadow: none; }
    .chevron { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); color: var(--sf-muted); pointer-events: none; }
    .list {
      position: absolute; z-index: 10; left: 0; right: 0; top: calc(100% + 4px); margin: 0; padding: 4px; list-style: none;
      max-height: 280px; overflow-y: auto; background: var(--sf-surface); border: 1px solid var(--sf-border-strong);
      border-radius: 8px; box-shadow: 0 6px 20px rgb(0 0 0 / 0.15);
    }
    li { padding: 8px 10px; border-radius: 6px; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
    li.active { background: var(--sf-primary-bg); }
    li[aria-selected="true"] .label { font-weight: 600; }
    .label { font-size: 14px; color: var(--sf-text); }
    .detail { font-size: 12px; color: var(--sf-muted); font-family: ui-monospace, monospace; word-break: break-all; }
    .empty { color: var(--sf-muted); cursor: default; font-size: 13px; }
  `;
}

customElements.define("sf-picker", SunflowPicker);
