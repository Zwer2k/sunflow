import { LitElement, html, css, nothing } from "../vendor/lit-all.min.js";

/**
 * Sunflow's own confirmation dialog instead of the browser's confirm().
 *
 * const ok = await confirmDialog({ title, text, confirm: "Einschalten", danger: false });
 */
class SunflowDialog extends LitElement {
  static properties = {
    heading: {},
    text: {},
    confirmLabel: {},
    cancelLabel: {},
    danger: { type: Boolean },
  };

  constructor() {
    super();
    this.heading = "";
    this.text = "";
    this.confirmLabel = "OK";
    this.cancelLabel = "Abbrechen";
    this.danger = false;
    this._resolve = null;
  }

  /** Show the dialog; resolves true on confirm, false on cancel, Escape or a click outside. */
  open() {
    return new Promise((resolve) => {
      this._resolve = resolve;
      this.updateComplete.then(() => {
        this.renderRoot.querySelector("dialog").showModal();
        this.renderRoot.querySelector(".confirm").focus();
      });
    });
  }

  _close(result) {
    this.renderRoot.querySelector("dialog")?.close();
    this._resolve?.(result);
    this._resolve = null;
    this.remove();
  }

  render() {
    return html`
      <dialog aria-labelledby="title" @cancel=${(e) => { e.preventDefault(); this._close(false); }}
        @click=${(e) => e.target.tagName === "DIALOG" && this._close(false)}>
        <div class="box">
          ${this.heading ? html`<h2 id="title">${this.heading}</h2>` : nothing}
          <p>${this.text}</p>
          <div class="actions">
            <button class="secondary" @click=${() => this._close(false)}>${this.cancelLabel}</button>
            <button class="confirm ${this.danger ? "danger" : ""}" @click=${() => this._close(true)}>${this.confirmLabel}</button>
          </div>
        </div>
      </dialog>
    `;
  }

  static styles = css`
    dialog {
      padding: 0; border: 0; border-radius: 12px; max-width: min(520px, calc(100vw - 32px));
      background: var(--sf-surface); color: var(--sf-text); box-shadow: 0 12px 40px rgb(0 0 0 / 0.25);
    }
    dialog::backdrop { background: rgb(0 0 0 / 0.4); }
    .box { padding: 20px 22px; display: flex; flex-direction: column; gap: 14px; }
    h2 { margin: 0; font-size: 17px; }
    p { margin: 0; font-size: 14px; line-height: 1.5; white-space: pre-line; }
    .actions { display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }
    button { font: inherit; min-height: 44px; padding: 0 18px; border-radius: 8px; cursor: pointer; font-weight: 600; }
    .secondary { border: 1px solid var(--sf-border-strong); background: var(--sf-surface); color: var(--sf-text); font-weight: 500; }
    .confirm { border: 0; background: var(--sf-primary); color: var(--sf-on-primary); }
    .confirm.danger { background: var(--sf-error-text); color: var(--sf-surface); }
    button:focus-visible { outline: 2px solid var(--sf-primary); outline-offset: 2px; }
  `;
}

customElements.define("sf-dialog", SunflowDialog);

export function confirmDialog({ title = "", text, confirm = "OK", cancel = "Abbrechen", danger = false }) {
  const dialog = document.createElement("sf-dialog");
  Object.assign(dialog, { heading: title, text, confirmLabel: confirm, cancelLabel: cancel, danger });
  document.body.append(dialog);
  return dialog.open();
}
