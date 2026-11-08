/** Forms and dialogs stay open when the dark backdrop is clicked. A quick
 *  double-click on the button that opens one would otherwise land its second
 *  click on the backdrop and close it at once, and a stray click outside would
 *  throw away what was typed. Close with Cancel, the × or Esc instead. */
export const STAY_OPEN = { mask: { closable: false } } as const;
