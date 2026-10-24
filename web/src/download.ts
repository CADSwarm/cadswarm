/** Hand bytes to the browser as a file download. */
export function saveBytes(bytes: Uint8Array, mime: string, filename: string) {
  const url = URL.createObjectURL(new Blob([bytes.slice()], { type: mime }));
  const link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}
