/** crt theme: phosphor/scanline switches from config, plus a blinking cursor. */

let root = null;

export function attach(boardsEl, options) {
  root = boardsEl;
  configure(options);
}

export function configure(options = {}) {
  const body = document.body;
  body.dataset.phosphor = options.phosphor || 'amber';
  body.dataset.scanlines = options.scanlines === false ? '0' : '1';
  body.dataset.curvature = options.curvature === false ? '0' : '1';
}

export function detach() {
  for (const cursor of document.querySelectorAll('.crt-cursor')) cursor.remove();
  for (const key of ['phosphor', 'scanlines', 'curvature']) delete document.body.dataset[key];
  root = null;
}

export function afterRender(boardsEl) {
  for (const header of boardsEl.querySelectorAll('.board-mode')) {
    if (!header.querySelector('.crt-cursor')) {
      const cursor = document.createElement('span');
      cursor.className = 'crt-cursor';
      header.append(cursor);
    }
  }
}
