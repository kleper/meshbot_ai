/**
 * Helpers ligeros progresivos para HTMX:
 * 1. Contador en vivo de bytes UTF-8 para paquetes LoRa Meshtastic (<= 228 bytes)
 * 2. Sincronización de pestaña activa (nav-tab) y chips de categoría
 */

function updateLoraByteCounter() {
  const titleEl = document.getElementById("titulo");
  const respEl = document.getElementById("respuesta");
  const badgeEl = document.getElementById("byte-counter");
  if (!titleEl || !respEl || !badgeEl) return;

  const title = titleEl.value.trim();
  const resp = respEl.value.trim();
  const preview = title ? `🤖 📌 ${title}: ${resp}` : `🤖 📌 ${resp}`;
  const bytes = new TextEncoder().encode(preview).length;
  const pkts = Math.max(1, Math.ceil(bytes / 220));

  badgeEl.textContent = `${bytes} / 228 bytes (${pkts} paq LoRa)`;
  badgeEl.className = bytes > 228 ? "byte-badge warn" : "byte-badge";
}

function bindDynamicControls() {
  const titleEl = document.getElementById("titulo");
  const respEl = document.getElementById("respuesta");
  if (titleEl && respEl) {
    titleEl.removeEventListener("input", updateLoraByteCounter);
    respEl.removeEventListener("input", updateLoraByteCounter);
    titleEl.addEventListener("input", updateLoraByteCounter);
    respEl.addEventListener("input", updateLoraByteCounter);
    updateLoraByteCounter();
  }

  // Marcar pestaña activa según el atributo data-active-tab del contenedor actual
  const tabMarker = document.querySelector("[data-active-tab]");
  if (tabMarker) {
    const currentTab = tabMarker.getAttribute("data-active-tab");
    document.querySelectorAll(".nav-tab").forEach((link) => {
      const linkTab = link.getAttribute("data-tab");
      link.classList.toggle("active", linkTab === currentTab);
      if (linkTab === currentTab) {
        link.setAttribute("aria-current", "page");
      } else {
        link.removeAttribute("aria-current");
      }
    });
  }
}

window.selectCategoryChip = function (btn, catValue) {
  const hiddenCat = document.getElementById("filter-categoria");
  if (hiddenCat) {
    hiddenCat.value = catValue;
  }
  document.querySelectorAll(".filter-chips .chip").forEach((c) => c.classList.remove("active"));
  btn.classList.add("active");
};

document.addEventListener("DOMContentLoaded", bindDynamicControls);
document.addEventListener("htmx:afterSwap", bindDynamicControls);
