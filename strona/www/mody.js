"use strict";

const el = (id) => document.getElementById(id);
let wiersze = [];
let aktywnyTag = null;

// Szukanie bez wielkosci liter i polskich znakow: "ksiez" znajdzie "Księżyce".
function uprosc(tekst) {
  return tekst.toLowerCase().replace(/ł/g, "l").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

function utworz(znacznik, klasa, tekst) {
  const w = document.createElement(znacznik);
  if (klasa) w.className = klasa;
  if (tekst) w.textContent = tekst;
  return w;
}

function wierszModa(mod) {
  const li = utworz("li");
  const szczegoly = utworz("details", "mod");
  const naglowek = utworz("summary");
  naglowek.append(utworz("span", "", mod.nazwa));
  for (const tag of mod.tagi) naglowek.append(utworz("span", "znacznik", tag));

  const tresc = utworz("div", "mod-tresc");
  for (const akapit of mod.opis) tresc.append(utworz("p", "", akapit));
  if (mod.link) {
    const a = utworz("a", "", `Zobacz na ${mod.zrodlo}`);
    a.href = mod.link;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    tresc.append(a);
  }
  szczegoly.append(naglowek, tresc);
  li.append(szczegoly);
  return { mod, li, szczegoly, klucz: uprosc(mod.nazwa) };
}

function odswiez() {
  const szukane = uprosc(el("szukaj").value.trim());
  const widoczne = wiersze.filter((w) => (
    w.klucz.includes(szukane) && (aktywnyTag === null || w.mod.tagi.includes(aktywnyTag))
  ));
  el("lista").replaceChildren(...widoczne.map((w) => w.li));
  el("licznik").textContent = widoczne.length
    ? `Modów: ${widoczne.length} z ${wiersze.length}`
    : "Nic nie znaleziono.";
  // Jedyny wynik, np. po wyborze podpowiedzi, od razu pokazuje opis.
  if (szukane && widoczne.length === 1) widoczne[0].szczegoly.open = true;
}

function utworzFiltry(tagi, mody) {
  const przyciski = [null, ...tagi].map((tag) => {
    const ile = tag === null ? mody.length : mody.filter((m) => m.tagi.includes(tag)).length;
    const przycisk = utworz("button", "filtr", `${tag ?? "Wszystkie"} (${ile})`);
    przycisk.type = "button";
    przycisk.setAttribute("aria-pressed", String(tag === null));
    przycisk.addEventListener("click", () => {
      aktywnyTag = tag;
      for (const p of przyciski) p.setAttribute("aria-pressed", String(p === przycisk));
      odswiez();
    });
    return przycisk;
  });
  el("filtry").replaceChildren(...przyciski);
}

async function start() {
  try {
    const odp = await fetch("/mody.json");
    if (!odp.ok) throw new Error(String(odp.status));
    const { tagi, mody } = await odp.json();
    wiersze = mody.map(wierszModa);
    utworzFiltry(tagi, mody);
    el("podpowiedzi").replaceChildren(...mody.map((m) => {
      const opcja = utworz("option");
      opcja.value = m.nazwa;
      return opcja;
    }));
    odswiez();
  } catch {
    el("licznik").textContent = "Nie udało się wczytać listy modów.";
  }
}

el("szukaj").addEventListener("input", odswiez);
start();
