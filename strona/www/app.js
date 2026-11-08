"use strict";

const OPISY = {
  wylaczony: ["Wyłączony", "Kliknij przycisk, a serwer sam się uruchomi. Start trwa kilka minut."],
  uruchamianie: ["Uruchamianie", "Serwer wstaje, zwykle trwa to kilka minut. Nie musisz nic więcej klikać."],
  wlaczony: ["Włączony", "Możesz wchodzić do gry."],
  zatrzymywanie: ["Wyłączanie", "Serwer zapisuje świat. Za chwilę znów będzie można go włączyć."],
  zablokowany: ["Zablokowany", "Na tym komputerze działa teraz coś innego, więc Minecrafta nie da się włączyć."],
  noc: ["Nocna przerwa", "W nocy serwera nie da się włączyć."],
  mapa: ["Aktualizacja mapy", "Serwer odświeża mapę świata. Gdy skończy, będzie można go włączyć."],
  brak_danych: ["Brak danych", "Nie udało się sprawdzić stanu serwera. Strona spróbuje ponownie sama."],
};
const CO_ILE_MS = 5000;

const el = (id) => document.getElementById(id);
let koniecOdliczania = null;
let wysylanie = false;

function pokazBlad(tekst) {
  el("blad").textContent = tekst;
  el("blad").hidden = !tekst;
}

function odswiezOdliczanie() {
  const pole = el("odliczanie");
  if (koniecOdliczania === null) {
    pole.hidden = true;
    return;
  }
  const s = Math.max(0, Math.round((koniecOdliczania - Date.now()) / 1000));
  const minuty = String(Math.floor(s / 60)).padStart(2, "0");
  const sekundy = String(s % 60).padStart(2, "0");
  pole.textContent = `Nikt nie gra. Serwer wyłączy się za ${minuty}:${sekundy}.`;
  pole.hidden = false;
}

function pokazGodziny(od, doGodz) {
  const pole = el("godziny");
  const krotko = (g) => g.replace(/^0(\d)/, "$1");
  if (od && doGodz) {
    pole.textContent = `Serwer można włączyć codziennie od ${krotko(od)} do ${krotko(doGodz)}.`;
    pole.hidden = false;
  }
}

function pokaz(dane) {
  const stan = OPISY[dane.stan] ? dane.stan : "brak_danych";
  document.body.dataset.stan = stan;
  el("stan-nazwa").textContent = OPISY[stan][0];
  el("stan-opis").textContent = OPISY[stan][1];
  document.title = `${OPISY[stan][0]}: Ci Panowie`;

  const dziala = stan === "wlaczony";
  el("gracze").hidden = !(dziala && dane.gracze !== null);
  if (dane.gracze !== null) {
    el("gracze-liczba").textContent = dane.max_graczy === null
      ? String(dane.gracze)
      : `${dane.gracze} / ${dane.max_graczy}`;
  }

  koniecOdliczania = dziala && dane.odliczanie_s !== null
    ? Date.now() + dane.odliczanie_s * 1000
    : null;
  odswiezOdliczanie();

  pokazGodziny(dane.czynny_od, dane.czynny_do);
  el("wlacz").hidden = stan !== "wylaczony";
  el("wlacz").disabled = wysylanie;
}

async function pobierz() {
  try {
    const odp = await fetch("/api/status", { cache: "no-store" });
    if (!odp.ok) throw new Error(String(odp.status));
    pokaz(await odp.json());
  } catch {
    pokaz({ stan: "brak_danych", gracze: null, max_graczy: null, odliczanie_s: null });
  }
}

async function wlacz() {
  wysylanie = true;
  el("wlacz").disabled = true;
  pokazBlad("");
  try {
    const odp = await fetch("/api/wlacz", { method: "POST" });
    if (odp.status === 503) {
      pokazBlad("Nie udało się wysłać polecenia. Spróbuj za chwilę.");
    }
  } catch {
    pokazBlad("Brak połączenia ze stroną. Sprawdź WireGuard i spróbuj ponownie.");
  }
  wysylanie = false;
  await pobierz();
}

async function kopiuj() {
  const przycisk = el("kopiuj");
  try {
    await navigator.clipboard.writeText(el("adres-gry").textContent);
    przycisk.textContent = "Skopiowano";
  } catch {
    przycisk.textContent = "Zaznacz ręcznie";
  }
  setTimeout(() => { przycisk.textContent = "Kopiuj"; }, 2000);
}

el("wlacz").addEventListener("click", wlacz);
el("kopiuj").addEventListener("click", kopiuj);
pobierz();
setInterval(pobierz, CO_ILE_MS);
setInterval(odswiezOdliczanie, 1000);
