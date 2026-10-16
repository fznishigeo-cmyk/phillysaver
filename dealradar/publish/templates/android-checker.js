function wpAndroidSharedURL(search) {
  const params = new URLSearchParams(search), direct = params.get("u");
  if (direct) return direct;
  for (const key of ["text", "url", "title"]) {
    const match = /https?:\/\/[^\s<>"\x00-\x1f]+/i.exec(params.get(key) || "");
    if (match) return match[0];
  }
  return "";
}
