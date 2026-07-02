/* Upload de poze direct browser -> R2, prin URL-uri presemnate de PUT.
   Serverul Flask nu mai atinge bytes de poze.

   Fluxul per poza: EXIF local (exifr, CDN) -> redimensionare canvas la
   max 2560px (JPEG 0.85) -> PUT original + PUT display -> register pe
   server (care face plasarea pe traseu dupa timestamp).

   PUT-urile folosesc XMLHttpRequest, nu fetch: fetch nu expune progres
   de upload, iar la poze de 5-15 MB progresul real conteaza. */
"use strict";

const PHOTO_MAX_DISPLAY_PX = 2560;
const PHOTO_JPEG_QUALITY = 0.85;
const PHOTO_UPLOAD_CONCURRENCY = 2;

/* EXIF brut: DateTimeOriginal ca string "YYYY:MM:DD HH:MM:SS" (serverul il
   parseaza la fel ca pana acum — ora locala + PHOTO_TZ_OFFSET_HOURS),
   GPS deja convertit in grade zecimale de exifr. */
async function photoReadExif(file) {
  const out = { takenAt: null, lat: null, lng: null };
  if (typeof exifr === "undefined") return out;
  try {
    const tags = await exifr.parse(file, {
      pick: ["DateTimeOriginal", "DateTime"], reviveValues: false,
    });
    if (tags) out.takenAt = tags.DateTimeOriginal || tags.DateTime || null;
  } catch (e) { /* EXIF ilizibil — continuam fara */ }
  try {
    const gps = await exifr.gps(file);
    if (gps && isFinite(gps.latitude) && isFinite(gps.longitude)) {
      out.lat = gps.latitude;
      out.lng = gps.longitude;
    }
  } catch (e) { /* fara GPS */ }
  return out;
}

async function photoDecodeImage(file) {
  try {
    // from-image aplica orientarea EXIF, deci JPEG-ul redimensionat iese drept
    return await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch (e) {
    const url = URL.createObjectURL(file);
    try {
      return await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error("imaginea nu poate fi decodată"));
        img.src = url;
      });
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 30000);
    }
  }
}

/* Varianta display: max 2560px pe latura lunga, JPEG ~0.85.
   Daca decodarea esueaza (ex: HEIC pe un browser fara suport), intoarce
   null si folosim originalul si ca display. */
async function photoMakeDisplayBlob(file) {
  let img;
  try {
    img = await photoDecodeImage(file);
  } catch (e) {
    return null;
  }
  const w = img.width, h = img.height;
  const scale = Math.min(1, PHOTO_MAX_DISPLAY_PX / Math.max(w, h));
  const cw = Math.max(1, Math.round(w * scale));
  const ch = Math.max(1, Math.round(h * scale));
  const canvas = document.createElement("canvas");
  canvas.width = cw;
  canvas.height = ch;
  canvas.getContext("2d").drawImage(img, 0, 0, cw, ch);
  if (img.close) img.close();
  return new Promise((resolve) =>
    canvas.toBlob((b) => resolve(b), "image/jpeg", PHOTO_JPEG_QUALITY));
}

function photoPut(url, blob, contentType, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    // Content-Type e in semnatura URL-ului presemnat — trebuie sa coincida
    xhr.setRequestHeader("Content-Type", contentType);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300)
      ? resolve()
      : reject(new Error("upload respins (HTTP " + xhr.status + ")"));
    xhr.onerror = () => reject(new Error("eroare de rețea la upload"));
    xhr.send(blob);
  });
}

async function photoPostJson(url, payload) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data = null;
  try { data = await r.json(); } catch (e) { /* raspuns ne-JSON */ }
  if (!r.ok) {
    throw new Error((data && data.error) || ("HTTP " + r.status));
  }
  return data;
}

/* UI: un rand per poza, cu bara de progres si stare/eroare. */
function photoUploadUi(listEl, files) {
  listEl.innerHTML = "";
  listEl.style.display = "block";
  return files.map((f) => {
    const row = document.createElement("div");
    row.className = "up-row";
    row.innerHTML =
      '<div class="up-name"></div>' +
      '<div class="up-track"><div class="up-bar"></div></div>' +
      '<div class="up-status">în așteptare</div>';
    row.querySelector(".up-name").textContent = f.name;
    listEl.appendChild(row);
    const bar = row.querySelector(".up-bar");
    const status = row.querySelector(".up-status");
    return {
      progress(frac) { bar.style.width = Math.round(frac * 100) + "%"; },
      state(text) { status.textContent = text; },
      error(text) {
        row.classList.add("up-failed");
        status.textContent = "eroare: " + text;
      },
      done() {
        row.classList.add("up-done");
        bar.style.width = "100%";
        status.textContent = "gata";
      },
    };
  });
}

/* Punctul de intrare: urca toate pozele pentru o tura.
   Erorile sunt per poza — restul continua. Intoarce {ok, failed}. */
async function uploadTrailPhotos(trailId, files, listEl) {
  const ui = photoUploadUi(listEl, files);
  const presigned = await photoPostJson(`/trail/${trailId}/photos/presign`, {
    files: files.map((f) => ({ name: f.name, type: f.type || "image/jpeg" })),
  });

  const registered = [];
  let failed = 0;

  async function uploadOne(i) {
    const file = files[i], grant = presigned.files[i], u = ui[i];
    try {
      u.state("citesc EXIF");
      const exif = await photoReadExif(file);

      u.state("redimensionez");
      const displayBlob = await photoMakeDisplayBlob(file);

      // progres combinat: originalul + varianta display, ponderate pe bytes
      const dispSize = displayBlob ? displayBlob.size : 0;
      const total = file.size + dispSize;

      u.state("urc originalul");
      await photoPut(grant.original_put_url, file, grant.original_content_type,
        (f) => u.progress((f * file.size) / total));

      if (displayBlob) {
        u.state("urc varianta redimensionată");
        await photoPut(grant.display_put_url, displayBlob, "image/jpeg",
          (f) => u.progress((file.size + f * dispSize) / total));
      } else {
        // nu am putut decoda (ex. HEIC) — originalul devine si display
        u.state("fără redimensionare (format nedecodabil)");
        await photoPut(grant.display_put_url, file, "image/jpeg", null);
      }

      registered.push({
        original_key: grant.original_key,
        display_key: grant.display_key,
        filename: file.name,
        taken_at: exif.takenAt,
        lat: exif.lat,
        lng: exif.lng,
      });
      u.done();
    } catch (e) {
      failed++;
      u.error(e.message || String(e));
    }
  }

  // pool mic: 2 poze in paralel — suficient pentru viteza, blând cu RAM-ul
  let next = 0;
  async function worker() {
    while (next < files.length) await uploadOne(next++);
  }
  await Promise.all(
    Array.from({ length: Math.min(PHOTO_UPLOAD_CONCURRENCY, files.length) }, worker));

  if (registered.length) {
    await photoPostJson(`/trail/${trailId}/photos/register`, { photos: registered });
  }
  return { ok: registered.length, failed };
}
