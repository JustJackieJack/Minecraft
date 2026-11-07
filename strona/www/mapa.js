"use strict";

// Ramka laduje sie dopiero, gdy mapa jest wgrana. Bez tego pokazalaby blad serwera.
async function start() {
  let jest = false;
  try {
    jest = (await fetch("/mapa-dane/settings.json", { method: "HEAD" })).ok;
  } catch {
    // Brak polaczenia traktujemy jak brak mapy.
  }
  document.getElementById("brak").hidden = jest;
  if (jest) {
    const ramka = document.getElementById("ramka");
    ramka.src = "/mapa-dane/";
    ramka.hidden = false;
  }
}

start();
