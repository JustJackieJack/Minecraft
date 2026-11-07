// Instalator paczki Ci_Panowie. Uruchamiany w folderze minecraft (tam, gdzie jest folder mods).
//
//	instalator             skiny, config i mody dla gracza
//	instalator --serwer    config i mody dla serwera, bez skinow i modow tylko dla gracza
//	instalator --tak       bez pytan
//	instalator --folder X  inny folder niz biezacy
//
// Niczego nie usuwa na stale: zastepowane i odrzucone pliki trafiaja do .ci_panowie_kopia.
package main

import (
	"archive/zip"
	"bufio"
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"hash"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	domyslnyAdres = "https://raw.githubusercontent.com/JustJackieJack/Minecraft/main/paczka/"
	plikStanu     = ".ci_panowie.json"
	folderKopii   = ".ci_panowie_kopia"
	folderTemp    = ".ci_panowie_tmp"
	stronaKlient  = "klient"
	rownoczesnie  = 4
	proby         = 3
)

// Archiwa moga zapisywac tylko w tych folderach.
var dozwoloneFoldery = map[string]bool{"config": true, "CustomSkinLoader": true, "defaultconfigs": true}

var wzorModId = regexp.MustCompile(`(?m)^\s*modId\s*=\s*"([^"]+)"`)

type Mod struct {
	Id     string `json:"id"`
	Nazwa  string `json:"nazwa"`
	Plik   string `json:"plik"`
	Sha1   string `json:"sha1"`
	Url    string `json:"url"`
	Strona string `json:"strona"`
}

type Archiwum struct {
	Plik   string `json:"plik"`
	Wersja string `json:"wersja"`
	Sha256 string `json:"sha256"`
}

type Paczka struct {
	Minecraft    string   `json:"minecraft"`
	Forge        string   `json:"forge"`
	Skiny        Archiwum `json:"skiny"`
	Config       Archiwum `json:"config"`
	ConfigSerwer Archiwum `json:"config_serwer"`
	Mody         []Mod    `json:"mody"`
}

type Instalator struct {
	folder string
	adres  string
	tak    bool
	klient *http.Client
	we     *Wejscie
	wy     io.Writer
	kopia  string
	bledy  []string
}

// Wejscie czyta odpowiedzi w tle, zeby pytanie dalo sie przerwac.
type Wejscie struct{ linie chan string }

func noweWejscie(r io.Reader) *Wejscie {
	w := &Wejscie{linie: make(chan string)}
	go func() {
		s := bufio.NewScanner(r)
		for s.Scan() {
			w.linie <- s.Text()
		}
		close(w.linie)
	}()
	return w
}

func (in *Instalator) log(format string, a ...any) { fmt.Fprintf(in.wy, format+"\n", a...) }

// pytaj zwraca pierwsza litere odpowiedzi. Koniec wejscia to odmowa, przerwanie to blad kontekstu.
func (in *Instalator) pytaj(ctx context.Context, pytanie, przyTak string) (string, error) {
	if in.tak {
		return przyTak, nil
	}
	fmt.Fprintf(in.wy, "%s ", pytanie)
	select {
	case linia, jest := <-in.we.linie:
		if !jest {
			return "n", nil
		}
		return strings.ToLower(strings.TrimSpace(linia) + " ")[:1], nil
	case <-ctx.Done():
		fmt.Fprintln(in.wy)
		return "", ctx.Err()
	}
}

func (in *Instalator) potwierdz(ctx context.Context, pytanie string) (bool, error) {
	o, err := in.pytaj(ctx, pytanie+" [t/N]", "t")
	return o == "t" || o == "y", err
}

func (in *Instalator) sciezka(wzgledna string) string {
	return filepath.Join(in.folder, filepath.FromSlash(wzgledna))
}

// adresPliku zamienia adres wzgledny z paczki na pelny.
func (in *Instalator) adresPliku(wzgledny string) (string, error) {
	baza, err := url.Parse(in.adres)
	if err != nil {
		return "", err
	}
	u, err := baza.Parse(wzgledny)
	if err != nil {
		return "", err
	}
	return u.String(), nil
}

func (in *Instalator) pobierzRaz(ctx context.Context, adres string, cel io.Writer) error {
	z, err := http.NewRequestWithContext(ctx, http.MethodGet, adres, nil)
	if err != nil {
		return err
	}
	z.Header.Set("User-Agent", "ci-panowie-instalator/1.0")
	o, err := in.klient.Do(z)
	if err != nil {
		return err
	}
	defer o.Body.Close()
	if o.StatusCode != http.StatusOK {
		return fmt.Errorf("HTTP %d", o.StatusCode)
	}
	_, err = io.Copy(cel, o.Body)
	return err
}

// pobierz zapisuje plik tymczasowy tylko wtedy, gdy zgadza sie suma.
func (in *Instalator) pobierz(ctx context.Context, adres, oczekiwana string, nowySkrot func() hash.Hash) (string, error) {
	if err := os.MkdirAll(in.sciezka(folderTemp), 0o755); err != nil {
		return "", err
	}
	var ostatni error
	for proba := 0; proba < proby && ctx.Err() == nil; proba++ {
		f, err := os.CreateTemp(in.sciezka(folderTemp), "pobierane-*")
		if err != nil {
			return "", err
		}
		s := nowySkrot()
		err = in.pobierzRaz(ctx, adres, io.MultiWriter(f, s))
		f.Close()
		if err == nil && hex.EncodeToString(s.Sum(nil)) != oczekiwana {
			err = errors.New("niezgodna suma kontrolna")
		}
		if err == nil {
			return f.Name(), nil
		}
		os.Remove(f.Name())
		ostatni = err
	}
	if ctx.Err() != nil {
		return "", ctx.Err()
	}
	return "", ostatni
}

func (in *Instalator) wczytajPaczke(ctx context.Context) (*Paczka, error) {
	adres, err := in.adresPliku("paczka.json")
	if err != nil {
		return nil, err
	}
	var b strings.Builder
	if err := in.pobierzRaz(ctx, adres, &b); err != nil {
		return nil, err
	}
	var p Paczka
	if err := json.Unmarshal([]byte(b.String()), &p); err != nil {
		return nil, err
	}
	for _, m := range p.Mody {
		if !poprawnaNazwaModa(m.Plik) {
			return nil, fmt.Errorf("niepoprawna nazwa moda: %q", m.Plik)
		}
	}
	return &p, nil
}

func poprawnaNazwaModa(nazwa string) bool {
	return strings.HasSuffix(nazwa, ".jar") && filepath.Base(nazwa) == nazwa && !strings.ContainsAny(nazwa, `/\`) && !strings.HasPrefix(nazwa, ".")
}

func (in *Instalator) wczytajStan() map[string]string {
	stan := map[string]string{}
	if d, err := os.ReadFile(in.sciezka(plikStanu)); err == nil {
		if json.Unmarshal(d, &stan) != nil {
			return map[string]string{}
		}
	}
	return stan
}

func (in *Instalator) zapiszStan(klucz, wersja string) error {
	stan := in.wczytajStan()
	stan[klucz] = wersja
	d, err := json.MarshalIndent(stan, "", " ")
	if err != nil {
		return err
	}
	tymczasowy := in.sciezka(plikStanu + ".nowy")
	if err := os.WriteFile(tymczasowy, d, 0o644); err != nil {
		return err
	}
	return os.Rename(tymczasowy, in.sciezka(plikStanu))
}

// doKopii przenosi plik do kopii zapasowej, zachowujac sciezke wzgledem folderu minecraft.
func (in *Instalator) doKopii(wzgledna string) error {
	cel := filepath.Join(in.folder, folderKopii, in.kopia, filepath.FromSlash(wzgledna))
	if err := os.MkdirAll(filepath.Dir(cel), 0o755); err != nil {
		return err
	}
	return os.Rename(in.sciezka(wzgledna), cel)
}

func bezpiecznaSciezka(nazwa string) bool {
	czesci := strings.Split(nazwa, "/")
	return filepath.IsLocal(filepath.FromSlash(nazwa)) && !strings.Contains(nazwa, `\`) && len(czesci) > 1 && dozwoloneFoldery[czesci[0]]
}

func (in *Instalator) rozpakujPlik(plik *zip.File) (zmieniony bool, err error) {
	r, err := plik.Open()
	if err != nil {
		return false, err
	}
	defer r.Close()
	dane, err := io.ReadAll(r)
	if err != nil {
		return false, err
	}
	cel := in.sciezka(plik.Name)
	if obecny, err := os.ReadFile(cel); err == nil {
		if string(obecny) == string(dane) {
			return false, nil
		}
		if err := in.doKopii(plik.Name); err != nil {
			return false, err
		}
	}
	if err := os.MkdirAll(filepath.Dir(cel), 0o755); err != nil {
		return false, err
	}
	return true, os.WriteFile(cel, dane, 0o644)
}

func (in *Instalator) rozpakuj(archiwum string) (int, error) {
	z, err := zip.OpenReader(archiwum)
	if err != nil {
		return 0, err
	}
	defer z.Close()
	// Najpierw sprawdzenie calosci: zle archiwum nie moze zostac zapisane w polowie.
	for _, f := range z.File {
		if f.FileInfo().IsDir() {
			continue
		}
		if !f.Mode().IsRegular() || !bezpiecznaSciezka(f.Name) {
			return 0, fmt.Errorf("niedozwolony plik w archiwum: %q", f.Name)
		}
	}
	zmienione := 0
	for _, f := range z.File {
		if f.FileInfo().IsDir() {
			continue
		}
		zmieniony, err := in.rozpakujPlik(f)
		if err != nil {
			return zmienione, err
		}
		if zmieniony {
			zmienione++
		}
	}
	return zmienione, nil
}

func (in *Instalator) sekcjaArchiwum(ctx context.Context, nazwa, klucz string, a Archiwum) error {
	obecna := in.wczytajStan()[klucz]
	if obecna == a.Wersja {
		in.log("%s: aktualne (wersja %s).", nazwa, a.Wersja)
		return nil
	}
	if obecna == "" {
		obecna = "brak"
	}
	in.log("%s: wersja u Ciebie: %s, w paczce: %s.", nazwa, obecna, a.Wersja)
	if tak, err := in.potwierdz(ctx, "Pobrać?"); err != nil || !tak {
		if err == nil {
			in.log("Pomijam.")
		}
		return err
	}
	adres, err := in.adresPliku(a.Plik)
	if err != nil {
		return err
	}
	tymczasowy, err := in.pobierz(ctx, adres, a.Sha256, sha256.New)
	if err != nil {
		return err
	}
	defer os.Remove(tymczasowy)
	zmienione, err := in.rozpakuj(tymczasowy)
	if err != nil {
		return err
	}
	in.log("%s: zmienionych plików: %d.", nazwa, zmienione)
	return in.zapiszStan(klucz, a.Wersja)
}

// Zamiana to starsza wersja moda z paczki, rozpoznana po modId.
type Zamiana struct {
	stary string
	nowy  Mod
}

type StanModow struct {
	poprawne  int
	brakujace []Mod
	zamiany   []Zamiana
	obce      []string
}

func modIdZJara(sciezka string) []string {
	z, err := zip.OpenReader(sciezka)
	if err != nil {
		return nil
	}
	defer z.Close()
	for _, f := range z.File {
		if f.Name != "META-INF/mods.toml" {
			continue
		}
		r, err := f.Open()
		if err != nil {
			return nil
		}
		defer r.Close()
		d, err := io.ReadAll(r)
		if err != nil {
			return nil
		}
		var ids []string
		for _, w := range wzorModId.FindAllSubmatch(d, -1) {
			ids = append(ids, string(w[1]))
		}
		return ids
	}
	return nil
}

func sumaSha1(sciezka string) (string, error) {
	f, err := os.Open(sciezka)
	if err != nil {
		return "", err
	}
	defer f.Close()
	s := sha1.New()
	if _, err := io.Copy(s, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(s.Sum(nil)), nil
}

func (in *Instalator) zbadajMody(lista []Mod) (StanModow, error) {
	wgSumy, wgId := map[string]Mod{}, map[string]Mod{}
	for _, m := range lista {
		wgSumy[m.Sha1] = m
		wgId[m.Id] = m
	}
	wpisy, err := filepath.Glob(filepath.Join(in.sciezka("mods"), "*.jar"))
	if err != nil {
		return StanModow{}, err
	}
	sort.Strings(wpisy)

	var stan StanModow
	maPoprawny := map[string]bool{}
	var pozostale []string
	for _, sciezka := range wpisy {
		suma, err := sumaSha1(sciezka)
		if err != nil {
			return stan, err
		}
		if m, jest := wgSumy[suma]; jest && !maPoprawny[m.Sha1] {
			maPoprawny[m.Sha1] = true
			stan.poprawne++
		} else {
			pozostale = append(pozostale, sciezka)
		}
	}
	zamieniane := map[string]bool{}
	for _, sciezka := range pozostale {
		nazwa := filepath.Base(sciezka)
		zamieniony := false
		for _, id := range modIdZJara(sciezka) {
			if m, jest := wgId[id]; jest && !maPoprawny[m.Sha1] && !zamieniane[m.Sha1] {
				stan.zamiany = append(stan.zamiany, Zamiana{nazwa, m})
				zamieniane[m.Sha1] = true
				zamieniony = true
				break
			}
		}
		if !zamieniony {
			stan.obce = append(stan.obce, nazwa)
		}
	}
	for _, m := range lista {
		if !maPoprawny[m.Sha1] && !zamieniane[m.Sha1] {
			stan.brakujace = append(stan.brakujace, m)
		}
	}
	return stan, nil
}

func (in *Instalator) usunObce(ctx context.Context, obce []string) error {
	if len(obce) == 0 {
		return nil
	}
	in.log("Mody spoza paczki (%d):", len(obce))
	for _, n := range obce {
		in.log("  %s", n)
	}
	in.log("Mogą powodować błędy, ale mogą też być Twoje, np. tylko po stronie gracza. Trafią do %s.", folderKopii)
	o, err := in.pytaj(ctx, "Usunąć? [t = wszystkie / w = wybiorę / N]", "t")
	if err != nil {
		return err
	}
	for _, n := range obce {
		usun := o == "t" || o == "y"
		if o == "w" {
			usun, err = in.potwierdz(ctx, "  Usunąć "+n+"?")
			if err != nil {
				return err
			}
		}
		if usun {
			if err := in.doKopii("mods/" + n); err != nil {
				return err
			}
			in.log("  usunięto %s", n)
		}
	}
	return nil
}

func (in *Instalator) zainstalujMod(ctx context.Context, m Mod, stary string) error {
	adres, err := in.adresPliku(m.Url)
	if err != nil {
		return err
	}
	tymczasowy, err := in.pobierz(ctx, adres, m.Sha1, sha1.New)
	if err != nil {
		return err
	}
	defer os.Remove(tymczasowy)
	// Stary plik znika dopiero, gdy nowy jest pobrany i sprawdzony.
	if stary != "" {
		if err := in.doKopii("mods/" + stary); err != nil {
			return err
		}
	}
	if _, err := os.Stat(in.sciezka("mods/" + m.Plik)); err == nil {
		if err := in.doKopii("mods/" + m.Plik); err != nil {
			return err
		}
	}
	return os.Rename(tymczasowy, in.sciezka("mods/"+m.Plik))
}

func (in *Instalator) pobierzMody(ctx context.Context, stan StanModow) {
	zadania := make(chan Zamiana)
	var wg sync.WaitGroup
	var blokada sync.Mutex
	gotowe, razem := 0, len(stan.zamiany)+len(stan.brakujace)
	for i := 0; i < rownoczesnie; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for z := range zadania {
				err := in.zainstalujMod(ctx, z.nowy, z.stary)
				blokada.Lock()
				gotowe++
				if err != nil {
					if ctx.Err() == nil {
						in.bledy = append(in.bledy, fmt.Sprintf("%s: %v", z.nowy.Plik, err))
						in.log("  BŁĄD %s: %v", z.nowy.Plik, err)
					}
				} else {
					in.log("  [%d/%d] %s", gotowe, razem, z.nowy.Plik)
				}
				blokada.Unlock()
			}
		}()
	}
	wyslij := func(z Zamiana) bool {
		select {
		case zadania <- z:
			return true
		case <-ctx.Done():
			return false
		}
	}
	for _, z := range stan.zamiany {
		if !wyslij(z) {
			break
		}
	}
	for _, m := range stan.brakujace {
		if !wyslij(Zamiana{nowy: m}) {
			break
		}
	}
	close(zadania)
	wg.Wait()
}

func (in *Instalator) sekcjaMody(ctx context.Context, wszystkie []Mod, serwer bool) error {
	var lista []Mod
	for _, m := range wszystkie {
		if !serwer || m.Strona != stronaKlient {
			lista = append(lista, m)
		}
	}
	if err := os.MkdirAll(in.sciezka("mods"), 0o755); err != nil {
		return err
	}
	stan, err := in.zbadajMody(lista)
	if err != nil {
		return err
	}
	in.log("Mody: poprawne %d z %d, do aktualizacji %d, brakujące %d, spoza paczki %d.",
		stan.poprawne, len(lista), len(stan.zamiany), len(stan.brakujace), len(stan.obce))
	if err := in.usunObce(ctx, stan.obce); err != nil {
		return err
	}
	if len(stan.zamiany)+len(stan.brakujace) == 0 {
		return nil
	}
	for _, z := range stan.zamiany {
		in.log("  %s -> %s", z.stary, z.nowy.Plik)
	}
	for _, m := range stan.brakujace {
		in.log("  nowy: %s", m.Plik)
	}
	tak, err := in.potwierdz(ctx, "Zaktualizować mody do wersji z paczki?")
	if err != nil || !tak {
		if err == nil {
			in.log("Pomijam.")
		}
		return err
	}
	in.pobierzMody(ctx, stan)
	return ctx.Err()
}

// Przerwania: Ctrl+C konczy biezacy krok, a dwa szybkie koncza program.
type Przerwania struct {
	blokada sync.Mutex
	anuluj  context.CancelFunc
}

func (p *Przerwania) ustaw(a context.CancelFunc) {
	p.blokada.Lock()
	p.anuluj = a
	p.blokada.Unlock()
}

func (p *Przerwania) sluchaj() {
	sygnaly := make(chan os.Signal, 2)
	signal.Notify(sygnaly, os.Interrupt)
	var ostatni time.Time
	for range sygnaly {
		if time.Since(ostatni) < 2*time.Second {
			os.Exit(130)
		}
		ostatni = time.Now()
		p.blokada.Lock()
		if p.anuluj != nil {
			p.anuluj()
		}
		p.blokada.Unlock()
	}
}

func (in *Instalator) krok(nazwa string, p *Przerwania, f func(context.Context) error) {
	in.log("\n== %s", nazwa)
	ctx, anuluj := context.WithCancel(context.Background())
	defer anuluj()
	p.ustaw(anuluj)
	err := f(ctx)
	p.ustaw(nil)
	switch {
	case errors.Is(err, context.Canceled) || ctx.Err() != nil:
		in.log("Przerwano ten krok, przechodzę dalej.")
	case err != nil:
		in.bledy = append(in.bledy, nazwa+": "+err.Error())
		in.log("BŁĄD: %v. Przechodzę dalej.", err)
	}
}

func wygladaNaFolderGry(folder string) bool {
	for _, n := range []string{"mods", "config", "options.txt", "server.properties"} {
		if _, err := os.Stat(filepath.Join(folder, n)); err == nil {
			return true
		}
	}
	return false
}

// wybierzFolder: przy dwukliku na macOS biezacy folder to katalog domowy, wiec sprawdzamy tez folder programu.
func wybierzFolder(podany string) (string, error) {
	if podany != "" {
		if !wygladaNaFolderGry(podany) {
			return "", fmt.Errorf("%s nie wygląda na folder minecraft", podany)
		}
		return filepath.Abs(podany)
	}
	kandydaci := []string{}
	if biezacy, err := os.Getwd(); err == nil {
		kandydaci = append(kandydaci, biezacy)
	}
	if program, err := os.Executable(); err == nil {
		kandydaci = append(kandydaci, filepath.Dir(program))
	}
	for _, k := range kandydaci {
		if wygladaNaFolderGry(k) {
			return k, nil
		}
	}
	return "", errors.New("uruchom instalator w folderze minecraft, tam gdzie jest folder mods")
}

func (in *Instalator) uruchom(serwer bool, p *Przerwania) {
	var paczka *Paczka
	in.krok("Paczka", p, func(ctx context.Context) (err error) {
		paczka, err = in.wczytajPaczke(ctx)
		if err == nil {
			in.log("Minecraft %s, Forge %s, modów: %d.", paczka.Minecraft, paczka.Forge, len(paczka.Mody))
		}
		return err
	})
	if paczka == nil {
		return
	}
	if serwer {
		in.krok("Tryb serwera", p, func(ctx context.Context) error {
			if os.Geteuid() == 0 {
				in.log("UWAGA: działasz jako root, a nowe pliki będą należały do roota. Uruchom jako właściciel serwera.")
			}
			tak, err := in.potwierdz(ctx, "Serwer musi być zatrzymany. Kontynuować?")
			// Przerwanie tutaj to tez odmowa: nie zmieniamy plikow dzialajacego serwera.
			if err != nil || !tak {
				paczka = nil
			}
			return err
		})
		if paczka == nil {
			return
		}
	} else {
		in.krok("Skiny", p, func(ctx context.Context) error {
			return in.sekcjaArchiwum(ctx, "Skiny", "skiny", paczka.Skiny)
		})
	}
	in.krok("Config", p, func(ctx context.Context) error {
		if serwer {
			return in.sekcjaArchiwum(ctx, "Config serwera", "config_serwer", paczka.ConfigSerwer)
		}
		return in.sekcjaArchiwum(ctx, "Config", "config", paczka.Config)
	})
	in.krok("Mody", p, func(ctx context.Context) error {
		return in.sekcjaMody(ctx, paczka.Mody, serwer)
	})
	os.RemoveAll(in.sciezka(folderTemp))
}

func main() {
	serwer := flag.Bool("serwer", false, "dane dla serwera zamiast gracza")
	tak := flag.Bool("tak", false, "bez pytan")
	folder := flag.String("folder", "", "folder minecraft, domyslnie biezacy")
	flag.Parse()

	adres := os.Getenv("MC_PACZKA_ADRES")
	if adres == "" {
		adres = domyslnyAdres
	}
	we := noweWejscie(os.Stdin)
	koniec := func(kod int) {
		if !*tak {
			fmt.Println("\nNaciśnij Enter, aby zamknąć.")
			<-we.linie
		}
		os.Exit(kod)
	}
	cel, err := wybierzFolder(*folder)
	if err != nil {
		fmt.Println("BŁĄD:", err)
		koniec(1)
	}
	fmt.Println("Folder:", cel)
	in := &Instalator{
		folder: cel, adres: adres, tak: *tak, we: we, wy: os.Stdout,
		klient: &http.Client{Transport: &http.Transport{Proxy: http.ProxyFromEnvironment, ResponseHeaderTimeout: 60 * time.Second}},
		kopia:  time.Now().Format("2006-01-02_150405"),
	}
	p := &Przerwania{}
	go p.sluchaj()
	in.uruchom(*serwer, p)

	if len(in.bledy) > 0 {
		fmt.Printf("\nZakończono z błędami (%d):\n", len(in.bledy))
		for _, b := range in.bledy {
			fmt.Println(" ", b)
		}
		koniec(1)
	}
	fmt.Println("\nGotowe.")
	koniec(0)
}
