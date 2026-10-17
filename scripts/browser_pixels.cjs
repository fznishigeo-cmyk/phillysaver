const assert = require('node:assert/strict');
const zlib = require('node:zlib');

// Chromium screenshots are non-interlaced, 8-bit RGB/RGBA PNGs. Decode with
// Node alone so the audit needs no image library or downloaded dependencies.
function pixels(png) {
  assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  let width, height, channels;
  const chunks = [];
  for (let at = 8; at < png.length;) {
    const length = png.readUInt32BE(at), type = png.toString('ascii', at + 4, at + 8);
    const data = png.subarray(at + 8, at + 8 + length);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      assert.equal(data[8], 8); assert.equal(data[12], 0);
      channels = {2: 3, 6: 4}[data[9]]; assert(channels, 'unsupported PNG color type');
    }
    if (type === 'IDAT') chunks.push(data);
    at += length + 12;
  }
  const raw = zlib.inflateSync(Buffer.concat(chunks)), stride = width * channels;
  const decoded = Buffer.alloc(height * stride);
  const paeth = (a, b, c) => {
    const p = a + b - c, da = Math.abs(p - a), db = Math.abs(p - b), dc = Math.abs(p - c);
    return da <= db && da <= dc ? a : db <= dc ? b : c;
  };
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)]; assert(filter <= 4);
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? decoded[y * stride + x - channels] : 0;
      const b = y ? decoded[(y - 1) * stride + x] : 0;
      const c = y && x >= channels ? decoded[(y - 1) * stride + x - channels] : 0;
      decoded[y * stride + x] = raw[y * (stride + 1) + x + 1] +
        [0, a, b, Math.floor((a + b) / 2), paeth(a, b, c)][filter];
    }
  }
  const data = Buffer.alloc(width * height * 3);
  for (let i = 0; i < width * height; i++) decoded.copy(data, i * 3, i * channels, i * channels + 3);
  return {width, height, data};
}

function glare(image) {
  let white = 0;
  for (let i = 0; i < image.data.length; i += 3)
    if (Math.min(...image.data.subarray(i, i + 3)) >= 245) white++;
  return white / (image.width * image.height);
}

// Uniform rows inside the content area, returned in document coordinates so
// a blank band spanning two viewport captures is still detected.
function blankBands(image, content, scrollY = 0) {
  const bands = [], left = Math.max(0, Math.ceil(content.left)), right = Math.min(image.width, Math.floor(content.right));
  if (right <= left) return bands;
  let start = null;
  const top = Math.max(0, Math.ceil(content.top)), bottom = Math.min(image.height, Math.floor(content.bottom));
  for (let y = top; y <= bottom; y++) {
    const colors = new Map(); let uniform = false;
    if (y < bottom) for (let x = left; x < right; x++) {
      const i = (y * image.width + x) * 3, color = image.data.readUIntBE(i, 3);
      const count = (colors.get(color) || 0) + 1; colors.set(color, count);
      if (count >= (right - left) * .98) {uniform = true; break;}
    }
    if (uniform && start === null) start = y;
    if (!uniform && start !== null) {bands.push({top: start + scrollY, bottom: y + scrollY}); start = null;}
  }
  return bands;
}

module.exports = {pixels, glare, blankBands};
