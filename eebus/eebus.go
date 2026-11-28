// The EEBUS side: Sunflow as energy manager (CEM) towards EEBUS wallboxes, with the use cases
// EV commissioning (EVCC), EV measurement (EVCEM), overload protection current limits (OPEV)
// and EV state of charge (EVSOC), built on enbility's eebus-go (MIT licence).
package main

import (
	"crypto/ecdsa"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"log"
	"net"
	"os"
	"path/filepath"
	"slices"
	"sync"
	"time"

	"github.com/enbility/eebus-go/api"
	"github.com/enbility/eebus-go/service"
	ucapi "github.com/enbility/eebus-go/usecases/api"
	"github.com/enbility/eebus-go/usecases/cem/evcc"
	"github.com/enbility/eebus-go/usecases/cem/evcem"
	"github.com/enbility/eebus-go/usecases/cem/evsoc"
	"github.com/enbility/eebus-go/usecases/cem/opev"
	shipapi "github.com/enbility/ship-go/api"
	"github.com/enbility/ship-go/cert"
	spineapi "github.com/enbility/spine-go/api"
	"github.com/enbility/spine-go/model"
	"github.com/enbility/zeroconf/v2"
)

// SHIP's usual port, when the wallbox's is not given
const defaultShipPort = 4712

var phaseNames = []model.ElectricalConnectionPhaseNameType{
	model.ElectricalConnectionPhaseNameTypeA,
	model.ElectricalConnectionPhaseNameTypeB,
	model.ElectricalConnectionPhaseNameTypeC,
}

// eebusBackend keeps what the wallboxes reported and writes their current limits.
type eebusBackend struct {
	mu        sync.Mutex
	svc       *service.Service
	ski       string
	trusted   *trustStore
	visible   map[string]shipapi.RemoteService
	connected map[string]bool
	// The EV entity of each wallbox while a car is plugged in
	evs map[string]spineapi.EntityRemoteInterface
	// Announcements of wallboxes in another network segment, by SKI
	proxies map[string]*zeroconf.Server
	evcc    ucapi.CemEVCCInterface
	evcem   ucapi.CemEVCEMInterface
	opev    ucapi.CemOPEVInterface
	evsoc   ucapi.CemEVSOCInterface
}

func newBackend(dataDir string, port int, logger *logger) (*eebusBackend, error) {
	certificate, err := loadOrCreateCertificate(dataDir)
	if err != nil {
		return nil, err
	}
	parsed, err := x509.ParseCertificate(certificate.Certificate[0])
	if err != nil {
		return nil, err
	}
	ski, err := cert.SkiFromCertificate(parsed)
	if err != nil {
		return nil, err
	}
	b := &eebusBackend{
		ski:       ski,
		trusted:   &trustStore{path: filepath.Join(dataDir, "trusted.json")},
		visible:   map[string]shipapi.RemoteService{},
		connected: map[string]bool{},
		evs:       map[string]spineapi.EntityRemoteInterface{},
		proxies:   map[string]*zeroconf.Server{},
	}
	if err := b.trusted.load(); err != nil {
		return nil, err
	}
	serial := ski[:8]
	configuration, err := api.NewConfiguration(
		"Sunflow", "Sunflow", "Sunflow EMS", serial,
		model.DeviceTypeTypeEnergyManagementSystem,
		[]model.EntityTypeType{model.EntityTypeTypeCEM},
		port, certificate, 4*time.Second,
	)
	if err != nil {
		return nil, err
	}
	configuration.SetAlternateIdentifier("Sunflow-EMS-" + serial)
	b.svc = service.NewService(configuration, b)
	b.svc.SetLogging(logger)
	if err := b.svc.Setup(); err != nil {
		return nil, err
	}
	local := b.svc.LocalDevice().EntityForType(model.EntityTypeTypeCEM)
	evccUC := evcc.NewEVCC(b.svc, local, b.onEvent)
	evcemUC := evcem.NewEVCEM(b.svc, local, b.onEvent)
	opevUC := opev.NewOPEV(local, b.onEvent)
	evsocUC := evsoc.NewEVSOC(local, b.onEvent)
	b.evcc, b.evcem, b.opev, b.evsoc = evccUC, evcemUC, opevUC, evsocUC
	for _, uc := range []api.UseCaseInterface{evccUC, evcemUC, opevUC, evsocUC} {
		b.svc.AddUseCase(uc)
	}
	for _, trusted := range b.trusted.list() {
		b.announce(trusted, b.trusted.address(trusted))
		b.svc.RegisterRemoteSKI(trusted)
	}
	return b, nil
}

func (b *eebusBackend) start() { b.svc.Start() }

func (b *eebusBackend) stop() {
	for ski := range b.proxies {
		b.announce(ski, Address{})
	}
	b.svc.Shutdown()
}

// onEvent follows cars being plugged in and out; everything else is read when asked.
func (b *eebusBackend) onEvent(ski string, _ spineapi.DeviceRemoteInterface, entity spineapi.EntityRemoteInterface, event api.EventType) {
	b.mu.Lock()
	defer b.mu.Unlock()
	switch event {
	case evcc.EvConnected:
		b.evs[ski] = entity
		log.Printf("Car plugged in at %s", ski)
	case evcc.EvDisconnected:
		delete(b.evs, ski)
		log.Printf("Car unplugged at %s", ski)
	}
}

func (b *eebusBackend) Status() Status {
	b.mu.Lock()
	defer b.mu.Unlock()
	services := map[string]Service{}
	for ski, remote := range b.visible {
		services[ski] = Service{SKI: ski, Name: remote.Name, Brand: remote.Brand, Model: remote.Model, Type: remote.Type}
	}
	for _, ski := range b.trusted.list() {
		if _, ok := services[ski]; !ok {
			services[ski] = Service{SKI: ski}
		}
	}
	result := Status{SKI: b.ski, Version: version, Services: []Service{}}
	for ski, s := range services {
		s.Trusted = b.trusted.has(ski)
		s.Connected = b.connected[ski]
		result.Services = append(result.Services, s)
	}
	slices.SortFunc(result.Services, func(x, y Service) int { return compare(x.SKI, y.SKI) })
	return result
}

func compare(x, y string) int {
	switch {
	case x < y:
		return -1
	case x > y:
		return 1
	}
	return 0
}

func (b *eebusBackend) Trust(ski string, address Address) error {
	if len(ski) != 40 {
		return fmt.Errorf("an SKI has 40 hex digits, got %d", len(ski))
	}
	if address.IP != "" && net.ParseIP(address.IP) == nil {
		return fmt.Errorf("%q is no IP address", address.IP)
	}
	if err := b.trusted.add(ski, address); err != nil {
		return err
	}
	b.announce(ski, address)
	b.svc.RegisterRemoteSKI(ski)
	log.Printf("Trusting %s", ski)
	return nil
}

func (b *eebusBackend) Untrust(ski string) error {
	if err := b.trusted.remove(ski); err != nil {
		return err
	}
	b.announce(ski, Address{})
	b.svc.UnregisterRemoteSKI(ski)
	log.Printf("No longer trusting %s", ski)
	return nil
}

// announce makes a wallbox in another network segment findable: mDNS does not cross segments,
// and eebus-go connects only to devices it found by mDNS. With an address given, the service
// announces the wallbox at that address in its own segment, and its own search connects there;
// TLS still checks the wallbox by its SKI. Without an address an announcement is withdrawn.
func (b *eebusBackend) announce(ski string, address Address) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if server := b.proxies[ski]; server != nil {
		server.Shutdown()
		delete(b.proxies, ski)
	}
	if address.IP == "" {
		return
	}
	port := address.Port
	if port == 0 {
		port = defaultShipPort
	}
	txt := []string{"txtvers=1", "id=" + ski, "path=/ship/", "ski=" + ski, "register=false"}
	server, err := zeroconf.RegisterProxy("Sunflow-"+ski[:8], "_ship._tcp", "local.", port, "eebus-"+ski[:8], []string{address.IP}, txt, nil)
	if err != nil {
		log.Printf("WARNING Announcing %s at %s failed: %v", ski, address.IP, err)
		return
	}
	b.proxies[ski] = server
	log.Printf("Announcing %s at %s:%d (other network segment)", ski, address.IP, port)
}

func (b *eebusBackend) Device(ski string) (Device, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if !b.trusted.has(ski) {
		return Device{}, false
	}
	device := Device{Connected: b.connected[ski]}
	ev := b.evs[ski]
	if ev == nil {
		return device, true
	}
	device.EVConnected = b.evcc.EVConnected(ev)
	if state, err := b.evcc.ChargeState(ev); err == nil {
		device.ChargeState = string(state)
		device.Charging = state == ucapi.EVChargeStateTypeActive
	}
	if powers, err := b.evcem.PowerPerPhase(ev); err == nil {
		for _, p := range powers {
			device.PowerW += p
		}
	}
	if energy, err := b.evcem.EnergyCharged(ev); err == nil {
		device.EnergyWh = energy
	}
	if phases, err := b.evcem.PhasesConnected(ev); err == nil {
		device.Phases = phases
	}
	if mins, maxs, _, err := b.opev.CurrentLimits(ev); err == nil && len(mins) > 0 && len(maxs) > 0 {
		device.MinCurrentA, device.MaxCurrentA = slices.Max(mins), slices.Min(maxs)
	}
	if limits, err := b.opev.LoadControlLimits(ev); err == nil && len(limits) > 0 && limits[0].IsActive {
		device.LimitA = &limits[0].Value
	}
	if soc, err := b.evsoc.StateOfCharge(ev); err == nil {
		device.SoCPct = &soc
	}
	return device, true
}

// SetLimit writes the same current on every phase; 0 A pauses charging.
func (b *eebusBackend) SetLimit(ski string, currentA float64) error {
	return b.writeLimits(ski, currentA, true)
}

// ReleaseLimit lifts Sunflow's limit: the wallbox charges as without an energy manager.
func (b *eebusBackend) ReleaseLimit(ski string) error {
	return b.writeLimits(ski, 0, false)
}

func (b *eebusBackend) writeLimits(ski string, currentA float64, active bool) error {
	b.mu.Lock()
	ev := b.evs[ski]
	b.mu.Unlock()
	if ev == nil {
		return errUnknownDevice
	}
	_, maxs, _, err := b.opev.CurrentLimits(ev)
	if err != nil {
		return fmt.Errorf("current limits of the car unknown: %w", err)
	}
	limits := make([]ucapi.LoadLimitsPhase, 0, len(maxs))
	for i := range min(len(maxs), len(phaseNames)) {
		value := currentA
		if !active || value > maxs[i] {
			value = maxs[i]
		}
		limits = append(limits, ucapi.LoadLimitsPhase{Phase: phaseNames[i], IsActive: active, Value: value})
	}
	if _, err := b.opev.WriteLoadControlLimits(ev, limits, nil); err != nil {
		return fmt.Errorf("writing the current limit failed: %w", err)
	}
	return nil
}

// ServiceReaderInterface of eebus-go

func (b *eebusBackend) RemoteSKIConnected(_ api.ServiceInterface, ski string) {
	b.mu.Lock()
	b.connected[ski] = true
	b.mu.Unlock()
	log.Printf("Connected to %s", ski)
}

func (b *eebusBackend) RemoteSKIDisconnected(_ api.ServiceInterface, ski string) {
	b.mu.Lock()
	b.connected[ski] = false
	delete(b.evs, ski)
	b.mu.Unlock()
	log.Printf("Disconnected from %s", ski)
}

func (b *eebusBackend) VisibleRemoteServicesUpdated(_ api.ServiceInterface, entries []shipapi.RemoteService) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.visible = map[string]shipapi.RemoteService{}
	for _, entry := range entries {
		b.visible[entry.Ski] = entry
	}
}

func (b *eebusBackend) ServiceShipIDUpdate(string, string) {}

func (b *eebusBackend) ServicePairingDetailUpdate(ski string, detail *shipapi.ConnectionStateDetail) {
	if detail != nil && detail.Error() != nil {
		log.Printf("Pairing with %s: %v", ski, detail.Error())
	}
}

// trustStore keeps the trusted wallboxes in the data directory: SKI and, for one in another
// network segment, its address.
type trustStore struct {
	mu      sync.Mutex
	path    string
	devices map[string]Address
}

type storedDevice struct {
	SKI string `json:"ski"`
	Address
}

func (t *trustStore) load() error {
	t.devices = map[string]Address{}
	data, err := os.ReadFile(t.path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	var stored struct {
		Devices []storedDevice `json:"devices"`
		// 0.1.0 kept the SKIs only
		SKIs []string `json:"skis"`
	}
	if err := json.Unmarshal(data, &stored); err != nil {
		return fmt.Errorf("%s unreadable: %w", t.path, err)
	}
	for _, ski := range stored.SKIs {
		t.devices[ski] = Address{}
	}
	for _, d := range stored.Devices {
		t.devices[d.SKI] = d.Address
	}
	return nil
}

func (t *trustStore) save() error {
	stored := struct {
		Devices []storedDevice `json:"devices"`
	}{Devices: []storedDevice{}}
	for _, ski := range t.sorted() {
		stored.Devices = append(stored.Devices, storedDevice{SKI: ski, Address: t.devices[ski]})
	}
	data, err := json.Marshal(stored)
	if err != nil {
		return err
	}
	return os.WriteFile(t.path, data, 0o600)
}

func (t *trustStore) sorted() []string {
	skis := make([]string, 0, len(t.devices))
	for ski := range t.devices {
		skis = append(skis, ski)
	}
	slices.Sort(skis)
	return skis
}

func (t *trustStore) list() []string {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.sorted()
}

func (t *trustStore) has(ski string) bool {
	t.mu.Lock()
	defer t.mu.Unlock()
	_, ok := t.devices[ski]
	return ok
}

func (t *trustStore) address(ski string) Address {
	t.mu.Lock()
	defer t.mu.Unlock()
	return t.devices[ski]
}

func (t *trustStore) add(ski string, address Address) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.devices[ski] = address
	return t.save()
}

func (t *trustStore) remove(ski string) error {
	t.mu.Lock()
	defer t.mu.Unlock()
	delete(t.devices, ski)
	return t.save()
}

// loadOrCreateCertificate keeps the service's identity: a new one would need pairing again.
func loadOrCreateCertificate(dataDir string) (tls.Certificate, error) {
	certPath, keyPath := filepath.Join(dataDir, "cert.pem"), filepath.Join(dataDir, "key.pem")
	if certificate, err := tls.LoadX509KeyPair(certPath, keyPath); err == nil {
		return certificate, nil
	}
	certificate, err := cert.CreateCertificate("Sunflow", "Sunflow", "DE", "Sunflow-EEBUS")
	if err != nil {
		return tls.Certificate{}, err
	}
	key, err := x509.MarshalECPrivateKey(certificate.PrivateKey.(*ecdsa.PrivateKey))
	if err != nil {
		return tls.Certificate{}, err
	}
	if err := os.WriteFile(certPath, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certificate.Certificate[0]}), 0o600); err != nil {
		return tls.Certificate{}, err
	}
	if err := os.WriteFile(keyPath, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: key}), 0o600); err != nil {
		return tls.Certificate{}, err
	}
	return certificate, nil
}
