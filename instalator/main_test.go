package main

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func zrobZip(t *testing.T, pliki map[string]string) []byte {
	t.Helper()
	var b bytes.Buffer
	z := zip.NewWriter(&b)
	for n, tresc := range pliki {
		w, err := z.Create(n)
		if err != nil {
			t.Fatal(err)
		}
		w.Write([]byte(tresc))
	}
	z.Close()
	return b.Bytes()
}

func jar(t *testing.T, modId, wariant string) []byte {
	return zrobZip(t, map[string]string{"META-INF/mods.toml": "[[mods]]\nmodId=\"" + modId + "\"\n", "x": wariant})
}

func s1(b []byte) string   { s := sha1.Sum(b); return hex.EncodeToString(s[:]) }
func s256(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }

type Scena struct {
	in        *Instalator
	dir       string
	pliki     map[string][]byte
	paczka    *Paczka
	wyjscie   *bytes.Buffer
	zapytania []string
}

func nowaScena(t *testing.T, odpowiedzi string) *Scena {
	t.Helper()
	s := &Scena{dir: t.TempDir(), pliki: map[string][]byte{}, wyjscie: &bytes.Buffer{}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.zapytania = append(s.zapytania, r.URL.Path)
		nazwa := strings.TrimPrefix(r.URL.Path, "/")
		if nazwa == "paczka.json" && s.paczka != nil {
			json.NewEncoder(w).Encode(s.paczka)
			return
		}
		if d, jest := s.pliki[nazwa]; jest {
			w.Write(d)
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(srv.Close)
	s.in = &Instalator{folder: s.dir, adres: srv.URL + "/", klient: srv.Client(), we: noweWejscie(strings.NewReader(odpowiedzi)),
		wy: s.wyjscie, kopia: "k"}
	if err := os.MkdirAll(filepath.Join(s.dir, "mods"), 0o755); err != nil {
		t.Fatal(err)
	}
	return s
}

func (s *Scena) mod(t *testing.T, id, plik, wariant, strona string) Mod {
	d := jar(t, id, wariant)
	s.pliki["pobierz/"+plik] = d
	return Mod{Id: id, Nazwa: id, Plik: plik, Sha1: s1(d), Url: "pobierz/" + plik, Strona: strona}
}

func (s *Scena) zapiszJar(t *testing.T, plik string, d []byte) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(s.dir, "mods", plik), d, 0o644); err != nil {
		t.Fatal(err)
	}
}

func istnieje(sciezka string) bool { _, err := os.Stat(sciezka); return err == nil }

func archiwum(s *Scena, t *testing.T, nazwa string, pliki map[string]string) Archiwum {
	d := zrobZip(t, pliki)
	s.pliki[nazwa] = d
	return Archiwum{Plik: nazwa, Wersja: s256(d)[:12], Sha256: s256(d)}
}

func TestPoprawneModyNicNieRobia(t *testing.T) {
	s := nowaScena(t, "")
	m := s.mod(t, "a", "a-1.jar", "1", "oba")
	s.zapiszJar(t, "a-1.jar", s.pliki["pobierz/a-1.jar"])
	if err := s.in.sekcjaMody(context.Background(), []Mod{m}, false); err != nil {
		t.Fatal(err)
	}
	if len(s.zapytania) != 0 {
		t.Fatalf("niepotrzebne pobieranie: %v", s.zapytania)
	}
}

func TestPoprawnyModOInnejNazwieZostaje(t *testing.T) {
	s := nowaScena(t, "")
	m := s.mod(t, "a", "a-1.jar", "1", "oba")
	s.zapiszJar(t, "inna.jar", s.pliki["pobierz/a-1.jar"])
	st, _ := s.in.zbadajMody([]Mod{m})
	if st.poprawne != 1 || len(st.obce)+len(st.brakujace)+len(st.zamiany) != 0 {
		t.Fatalf("%+v", st)
	}
}

func TestAktualizacjaIBrakujacy(t *testing.T) {
	s := nowaScena(t, "t\n")
	nowy := s.mod(t, "a", "a-2.jar", "2", "oba")
	brak := s.mod(t, "b", "b-1.jar", "1", "oba")
	s.zapiszJar(t, "a-1.jar", jar(t, "a", "1"))
	if err := s.in.sekcjaMody(context.Background(), []Mod{nowy, brak}, false); err != nil {
		t.Fatal(err)
	}
	if !istnieje(filepath.Join(s.dir, "mods", "a-2.jar")) || !istnieje(filepath.Join(s.dir, "mods", "b-1.jar")) {
		t.Fatal("brak nowych plikow")
	}
	if istnieje(filepath.Join(s.dir, "mods", "a-1.jar")) || !istnieje(filepath.Join(s.dir, folderKopii, "k", "mods", "a-1.jar")) {
		t.Fatal("stara wersja nie trafila do kopii")
	}
}

func TestOdmowaAktualizacjiZostawiaStaryMod(t *testing.T) {
	s := nowaScena(t, "n\n")
	nowy := s.mod(t, "a", "a-2.jar", "2", "oba")
	s.zapiszJar(t, "a-1.jar", jar(t, "a", "1"))
	if err := s.in.sekcjaMody(context.Background(), []Mod{nowy}, false); err != nil {
		t.Fatal(err)
	}
	if !istnieje(filepath.Join(s.dir, "mods", "a-1.jar")) || istnieje(filepath.Join(s.dir, "mods", "a-2.jar")) {
		t.Fatal("odmowa zmienila pliki")
	}
}

func TestBlednaSumaNieUsuwaStaregoModa(t *testing.T) {
	s := nowaScena(t, "t\n")
	nowy := s.mod(t, "a", "a-2.jar", "2", "oba")
	s.pliki["pobierz/a-2.jar"] = []byte("zepsute")
	s.zapiszJar(t, "a-1.jar", jar(t, "a", "1"))
	s.in.sekcjaMody(context.Background(), []Mod{nowy}, false)
	if !istnieje(filepath.Join(s.dir, "mods", "a-1.jar")) || istnieje(filepath.Join(s.dir, "mods", "a-2.jar")) {
		t.Fatal("zly plik zastapil mod")
	}
	if len(s.in.bledy) != 1 {
		t.Fatalf("bledy: %v", s.in.bledy)
	}
}

func TestObceWyborPojedynczy(t *testing.T) {
	s := nowaScena(t, "w\nt\nn\n")
	s.zapiszJar(t, "x.jar", jar(t, "x", "1"))
	s.zapiszJar(t, "y.jar", jar(t, "y", "1"))
	if err := s.in.sekcjaMody(context.Background(), nil, false); err != nil {
		t.Fatal(err)
	}
	if istnieje(filepath.Join(s.dir, "mods", "x.jar")) || !istnieje(filepath.Join(s.dir, "mods", "y.jar")) {
		t.Fatal("wybor nie zadzialal")
	}
}

func TestObceDomyslnieZostaja(t *testing.T) {
	s := nowaScena(t, "\n")
	s.zapiszJar(t, "x.jar", jar(t, "x", "1"))
	s.in.sekcjaMody(context.Background(), nil, false)
	if !istnieje(filepath.Join(s.dir, "mods", "x.jar")) {
		t.Fatal("usunieto bez zgody")
	}
}

func TestSerwerNieInstalujeModowGraczaIProponujeIchUsuniecie(t *testing.T) {
	s := nowaScena(t, "t\nt\n")
	klient := s.mod(t, "k", "k.jar", "1", stronaKlient)
	oba := s.mod(t, "o", "o.jar", "1", "oba")
	s.zapiszJar(t, "k.jar", s.pliki["pobierz/k.jar"])
	if err := s.in.sekcjaMody(context.Background(), []Mod{klient, oba}, true); err != nil {
		t.Fatal(err)
	}
	if istnieje(filepath.Join(s.dir, "mods", "k.jar")) || !istnieje(filepath.Join(s.dir, "mods", "o.jar")) {
		t.Fatal("zly zestaw modow na serwerze")
	}
}

func TestArchiwumWersjaIKopia(t *testing.T) {
	s := nowaScena(t, "t\n")
	a := archiwum(s, t, "config.zip", map[string]string{"config/a.toml": "nowe", "config/b/c.toml": "x"})
	stary := filepath.Join(s.dir, "config", "a.toml")
	os.MkdirAll(filepath.Dir(stary), 0o755)
	os.WriteFile(stary, []byte("stare"), 0o644)
	if err := s.in.sekcjaArchiwum(context.Background(), "Config", "config", a); err != nil {
		t.Fatal(err)
	}
	d, _ := os.ReadFile(stary)
	k, _ := os.ReadFile(filepath.Join(s.dir, folderKopii, "k", "config", "a.toml"))
	if string(d) != "nowe" || string(k) != "stare" {
		t.Fatalf("plik %q, kopia %q", d, k)
	}
	if s.in.wczytajStan()["config"] != a.Wersja {
		t.Fatal("wersja niezapisana")
	}
	liczba := len(s.zapytania)
	if err := s.in.sekcjaArchiwum(context.Background(), "Config", "config", a); err != nil {
		t.Fatal(err)
	}
	if len(s.zapytania) != liczba {
		t.Fatal("aktualne archiwum pobrane ponownie")
	}
}

func TestOdmowaArchiwumNicNiePobiera(t *testing.T) {
	s := nowaScena(t, "n\n")
	a := archiwum(s, t, "skiny.zip", map[string]string{"CustomSkinLoader/a.json": "x"})
	if err := s.in.sekcjaArchiwum(context.Background(), "Skiny", "skiny", a); err != nil {
		t.Fatal(err)
	}
	if len(s.zapytania) != 0 || s.in.wczytajStan()["skiny"] != "" {
		t.Fatal("odmowa nie zadzialala")
	}
}

func TestZlaSumaArchiwum(t *testing.T) {
	s := nowaScena(t, "t\n")
	a := archiwum(s, t, "c.zip", map[string]string{"config/a": "x"})
	a.Sha256 = strings.Repeat("0", 64)
	if err := s.in.sekcjaArchiwum(context.Background(), "C", "c", a); err == nil {
		t.Fatal("brak bledu")
	}
	if istnieje(filepath.Join(s.dir, "config", "a")) || s.in.wczytajStan()["c"] != "" {
		t.Fatal("zapisano mimo zlej sumy")
	}
}

func TestArchiwumNieWychodziPozaDozwoloneFoldery(t *testing.T) {
	for _, zla := range []string{"../zlo", "mods/zly.jar", "config/../../zlo", "/etc/zlo", "plik", `config\..\zlo`} {
		s := nowaScena(t, "t\n")
		a := archiwum(s, t, "c.zip", map[string]string{"config/dobry": "x", zla: "zlo"})
		if err := s.in.sekcjaArchiwum(context.Background(), "C", "c", a); err == nil {
			t.Errorf("%q: brak bledu", zla)
		}
		if istnieje(filepath.Join(s.dir, "config", "dobry")) {
			t.Errorf("%q: czesc archiwum zapisana", zla)
		}
	}
}

func TestPrzerwaniePytaniaNieWisi(t *testing.T) {
	r, w := io.Pipe()
	defer w.Close()
	s := nowaScena(t, "")
	s.in.we = noweWejscie(r)
	ctx, anuluj := context.WithCancel(context.Background())
	anuluj()
	if _, err := s.in.pytaj(ctx, "?", "t"); err == nil {
		t.Fatal("brak bledu przerwania")
	}
}

func TestKoniecWejsciaToOdmowa(t *testing.T) {
	s := nowaScena(t, "")
	tak, err := s.in.potwierdz(context.Background(), "?")
	if err != nil || tak {
		t.Fatal("koniec wejscia powinien byc odmowa")
	}
}

func TestZlaNazwaModaOdrzucaPaczke(t *testing.T) {
	for _, zla := range []string{"../zly.jar", "a/b.jar", `a\b.jar`, "x.exe", ".jar"} {
		s := nowaScena(t, "")
		s.paczka = &Paczka{Mody: []Mod{{Plik: zla}}}
		if _, err := s.in.wczytajPaczke(context.Background()); err == nil {
			t.Errorf("%q przyjeta", zla)
		}
	}
}

func TestBladJednejSekcjiNieKonczyPozostalych(t *testing.T) {
	s := nowaScena(t, "t\nt\n")
	m := s.mod(t, "a", "a.jar", "1", "oba")
	s.paczka = &Paczka{Skiny: Archiwum{Plik: "nie-ma.zip", Wersja: "1", Sha256: "0"},
		Config: archiwum(s, t, "c.zip", map[string]string{"config/a": "x"}), Mody: []Mod{m}}
	s.in.tak = true
	s.in.uruchom(false, &Przerwania{})
	if !istnieje(filepath.Join(s.dir, "config", "a")) || !istnieje(filepath.Join(s.dir, "mods", "a.jar")) {
		t.Fatal("kolejne kroki sie nie wykonaly")
	}
	if len(s.in.bledy) != 1 || !strings.Contains(s.in.bledy[0], "Skiny") {
		t.Fatalf("bledy: %v", s.in.bledy)
	}
}

func TestSerwerPomijaSkinyIUzywaSwojegoConfigu(t *testing.T) {
	s := nowaScena(t, "")
	s.in.tak = true
	s.paczka = &Paczka{Skiny: archiwum(s, t, "s.zip", map[string]string{"CustomSkinLoader/a": "x"}),
		Config:       archiwum(s, t, "c.zip", map[string]string{"config/gracz": "x"}),
		ConfigSerwer: archiwum(s, t, "cs.zip", map[string]string{"config/serwer": "x", "defaultconfigs/d": "x"})}
	s.in.uruchom(true, &Przerwania{})
	if istnieje(filepath.Join(s.dir, "CustomSkinLoader", "a")) || istnieje(filepath.Join(s.dir, "config", "gracz")) {
		t.Fatal("serwer dostal dane gracza")
	}
	if !istnieje(filepath.Join(s.dir, "defaultconfigs", "d")) {
		t.Fatal("brak configu serwera")
	}
}

func TestSerwerBezZgodyNicNieZmienia(t *testing.T) {
	s := nowaScena(t, "n\n")
	s.paczka = &Paczka{ConfigSerwer: archiwum(s, t, "cs.zip", map[string]string{"config/serwer": "x"})}
	s.in.uruchom(true, &Przerwania{})
	if istnieje(filepath.Join(s.dir, "config", "serwer")) {
		t.Fatal("zmieniono pliki mimo odmowy")
	}
}

func TestWyborFolderu(t *testing.T) {
	d := t.TempDir()
	if _, err := wybierzFolder(d); err == nil {
		t.Fatal("pusty folder przyjety")
	}
	os.Mkdir(filepath.Join(d, "mods"), 0o755)
	if _, err := wybierzFolder(d); err != nil {
		t.Fatal(err)
	}
}

func TestOpcjonalnyPytaIInstalujePoZgodzie(t *testing.T) {
	s := nowaScena(t, "t\nt\n")
	m := s.mod(t, "dh", "dh-1.jar", "1", stronaKlient)
	m.Opcjonalny = true
	if err := s.in.sekcjaMody(context.Background(), []Mod{m}, false); err != nil {
		t.Fatal(err)
	}
	if !istnieje(filepath.Join(s.dir, "mods", "dh-1.jar")) {
		t.Fatal("opcjonalny mod nie zostal zainstalowany mimo zgody")
	}
}

func TestOpcjonalnyBezZgodyPominiety(t *testing.T) {
	s := nowaScena(t, "\n")
	m := s.mod(t, "dh", "dh-1.jar", "1", stronaKlient)
	m.Opcjonalny = true
	if err := s.in.sekcjaMody(context.Background(), []Mod{m}, false); err != nil {
		t.Fatal(err)
	}
	if istnieje(filepath.Join(s.dir, "mods", "dh-1.jar")) || len(s.zapytania) != 0 {
		t.Fatal("opcjonalny mod zainstalowany bez zgody")
	}
}

func TestOpcjonalnyBezPytanNieJestDodawany(t *testing.T) {
	s := nowaScena(t, "")
	s.in.tak = true
	m := s.mod(t, "dh", "dh-1.jar", "1", stronaKlient)
	m.Opcjonalny = true
	s.in.sekcjaMody(context.Background(), []Mod{m}, false)
	if istnieje(filepath.Join(s.dir, "mods", "dh-1.jar")) {
		t.Fatal("--tak dodal mod opcjonalny")
	}
}

func TestOpcjonalnyZainstalowanyJestAktualizowanyBezPytania(t *testing.T) {
	s := nowaScena(t, "t\n")
	m := s.mod(t, "dh", "dh-2.jar", "2", stronaKlient)
	m.Opcjonalny = true
	s.zapiszJar(t, "dh-1.jar", jar(t, "dh", "1"))
	if err := s.in.sekcjaMody(context.Background(), []Mod{m}, false); err != nil {
		t.Fatal(err)
	}
	if !istnieje(filepath.Join(s.dir, "mods", "dh-2.jar")) || istnieje(filepath.Join(s.dir, "mods", "dh-1.jar")) {
		t.Fatal("zainstalowany mod opcjonalny nie zostal zaktualizowany")
	}
}

func TestSerwerPomijaOpcjonalne(t *testing.T) {
	s := nowaScena(t, "t\n")
	m := s.mod(t, "dh", "dh-1.jar", "1", "oba")
	m.Opcjonalny = true
	s.in.sekcjaMody(context.Background(), []Mod{m}, true)
	if istnieje(filepath.Join(s.dir, "mods", "dh-1.jar")) {
		t.Fatal("serwer dostal mod opcjonalny")
	}
}

func TestUsunietyZPaczkiDomyslnieUsuwany(t *testing.T) {
	s := nowaScena(t, "\n")
	s.in.usuniete = map[string]string{"biomemusic": "Biome Music"}
	s.zapiszJar(t, "biomemusic-4.1.jar", jar(t, "biomemusic", "1"))
	s.zapiszJar(t, "moj.jar", jar(t, "moj", "1"))
	if err := s.in.sekcjaMody(context.Background(), nil, false); err != nil {
		t.Fatal(err)
	}
	if istnieje(filepath.Join(s.dir, "mods", "biomemusic-4.1.jar")) {
		t.Fatal("mod usuniety z paczki zostal")
	}
	if !istnieje(filepath.Join(s.dir, "mods", "moj.jar")) {
		t.Fatal("obcy mod usuniety bez zgody")
	}
}

func TestUsunietyZPaczkiOdmowaZostawia(t *testing.T) {
	s := nowaScena(t, "n\n")
	s.in.usuniete = map[string]string{"biomemusic": "Biome Music"}
	s.zapiszJar(t, "biomemusic-4.1.jar", jar(t, "biomemusic", "1"))
	s.in.sekcjaMody(context.Background(), nil, false)
	if !istnieje(filepath.Join(s.dir, "mods", "biomemusic-4.1.jar")) {
		t.Fatal("usunieto mimo odmowy")
	}
}

func TestConfigGryPierwszaInstalacjaDomyslnieTak(t *testing.T) {
	s := nowaScena(t, "\n")
	a := archiwum(s, t, "c.zip", map[string]string{"config/a.toml": "nowe"})
	if err := s.in.sekcjaConfiguGry(context.Background(), a); err != nil {
		t.Fatal(err)
	}
	if !istnieje(filepath.Join(s.dir, "config", "a.toml")) || s.in.wczytajStan()["config"] != a.Wersja {
		t.Fatal("pierwsza instalacja nie pobrala configu")
	}
}

func TestConfigGryPozniejDomyslnieNie(t *testing.T) {
	for _, tak := range []bool{false, true} {
		s := nowaScena(t, "\n")
		s.in.tak = tak
		s.in.zapiszStan("config", "stara")
		stary := filepath.Join(s.dir, "config", "a.toml")
		os.MkdirAll(filepath.Dir(stary), 0o755)
		os.WriteFile(stary, []byte("moje"), 0o644)
		a := archiwum(s, t, "c.zip", map[string]string{"config/a.toml": "nowe"})
		if err := s.in.sekcjaConfiguGry(context.Background(), a); err != nil {
			t.Fatal(err)
		}
		if d, _ := os.ReadFile(stary); string(d) != "moje" || len(s.zapytania) != 0 {
			t.Fatalf("--tak=%v: config gracza zastapiony bez zgody", tak)
		}
	}
}

func TestConfigGryPozniejNaZyczenie(t *testing.T) {
	s := nowaScena(t, "t\n")
	s.in.zapiszStan("config", "stara")
	a := archiwum(s, t, "c.zip", map[string]string{"config/a.toml": "nowe"})
	s.in.sekcjaConfiguGry(context.Background(), a)
	if d, _ := os.ReadFile(filepath.Join(s.dir, "config", "a.toml")); string(d) != "nowe" {
		t.Fatal("zgoda nie pobrala configu")
	}
}

func TestUstawieniaGraczaTylkoBrakujace(t *testing.T) {
	s := nowaScena(t, "")
	moj := filepath.Join(s.dir, "config", "grafika.json")
	os.MkdirAll(filepath.Dir(moj), 0o755)
	os.WriteFile(moj, []byte("moje"), 0o644)
	a := archiwum(s, t, "o.zip", map[string]string{"config/grafika.json": "paczka", "config/nowy-mod-client.toml": "x"})
	if err := s.in.sekcjaUstawienGracza(context.Background(), a); err != nil {
		t.Fatal(err)
	}
	if d, _ := os.ReadFile(moj); string(d) != "moje" {
		t.Fatal("nadpisano ustawienia gracza")
	}
	if !istnieje(filepath.Join(s.dir, "config", "nowy-mod-client.toml")) || s.in.wczytajStan()["config_osobisty"] != a.Wersja {
		t.Fatal("brakujacy plik nie zostal dodany")
	}
	s.zapytania = nil
	s.in.sekcjaUstawienGracza(context.Background(), a)
	if len(s.zapytania) != 0 {
		t.Fatal("ta sama wersja pobrana ponownie")
	}
	if err := s.in.sekcjaUstawienGracza(context.Background(), Archiwum{}); err != nil {
		t.Fatal(err)
	}
}

func TestSerwerNieDostajeUstawienGracza(t *testing.T) {
	s := nowaScena(t, "")
	s.in.tak = true
	s.paczka = &Paczka{ConfigSerwer: archiwum(s, t, "cs.zip", map[string]string{"config/serwer": "x"}),
		ConfigOsobisty: archiwum(s, t, "o.zip", map[string]string{"config/grafika.json": "x"})}
	s.in.uruchom(true, &Przerwania{})
	if istnieje(filepath.Join(s.dir, "config", "grafika.json")) {
		t.Fatal("serwer dostal ustawienia gracza")
	}
}
