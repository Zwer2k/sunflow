// Local HTTP API of the EEBUS service for Sunflow. It listens on the Supervisor's network only,
// so it is not reachable from the home network; Sunflow reads it like a directly connected device.
package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
)

// Service is a found or trusted EEBUS device.
type Service struct {
	SKI       string `json:"ski"`
	Name      string `json:"name"`
	Brand     string `json:"brand"`
	Model     string `json:"model"`
	Type      string `json:"type"`
	Trusted   bool   `json:"trusted"`
	Connected bool   `json:"connected"`
}

// Status of the service: its own SKI (entered in the wallbox's app) and the devices around.
type Status struct {
	SKI      string    `json:"ski"`
	Version  string    `json:"version"`
	Services []Service `json:"services"`
}

// Device is what Sunflow reads of one wallbox (points of the eebus/wallbox profile).
type Device struct {
	Connected   bool     `json:"connected"`
	EVConnected bool     `json:"ev_connected"`
	ChargeState string   `json:"charge_state"`
	Charging    bool     `json:"charging"`
	PowerW      float64  `json:"power_w"`
	EnergyWh    float64  `json:"energy_wh"`
	Phases      uint     `json:"phases"`
	MinCurrentA float64  `json:"min_current_a"`
	MaxCurrentA float64  `json:"max_current_a"`
	LimitA      *float64 `json:"limit_a"`
	SoCPct      *float64 `json:"soc_pct"`
}

// Backend is the EEBUS side; a fake in the tests.
type Backend interface {
	Status() Status
	Trust(ski string) error
	Untrust(ski string) error
	Device(ski string) (Device, bool)
	SetLimit(ski string, currentA float64) error
	ReleaseLimit(ski string) error
}

var errUnknownDevice = errors.New("unknown or not connected device")

// NewHandler returns the API: GET /status, POST/DELETE /trust/{ski},
// GET /devices/{ski}, POST/DELETE /devices/{ski}/limit (body {"current_a": 10}).
func NewHandler(b Backend) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /status", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, b.Status())
	})
	mux.HandleFunc("POST /trust/{ski}", func(w http.ResponseWriter, r *http.Request) {
		answer(w, b.Trust(normalizeSKI(r.PathValue("ski"))))
	})
	mux.HandleFunc("DELETE /trust/{ski}", func(w http.ResponseWriter, r *http.Request) {
		answer(w, b.Untrust(normalizeSKI(r.PathValue("ski"))))
	})
	mux.HandleFunc("GET /devices/{ski}", func(w http.ResponseWriter, r *http.Request) {
		device, ok := b.Device(normalizeSKI(r.PathValue("ski")))
		if !ok {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": errUnknownDevice.Error()})
			return
		}
		writeJSON(w, http.StatusOK, device)
	})
	mux.HandleFunc("POST /devices/{ski}/limit", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			CurrentA *float64 `json:"current_a"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.CurrentA == nil || *body.CurrentA < 0 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "current_a (A, >= 0) required"})
			return
		}
		answer(w, b.SetLimit(normalizeSKI(r.PathValue("ski")), *body.CurrentA))
	})
	mux.HandleFunc("DELETE /devices/{ski}/limit", func(w http.ResponseWriter, r *http.Request) {
		answer(w, b.ReleaseLimit(normalizeSKI(r.PathValue("ski"))))
	})
	return mux
}

// normalizeSKI accepts the SKI as shown by devices: with spaces or dashes, any case.
func normalizeSKI(ski string) string {
	return strings.ToLower(strings.NewReplacer(" ", "", "-", "", ":", "").Replace(ski))
}

func answer(w http.ResponseWriter, err error) {
	switch {
	case err == nil:
		writeJSON(w, http.StatusOK, map[string]string{"result": "ok"})
	case errors.Is(err, errUnknownDevice):
		writeJSON(w, http.StatusNotFound, map[string]string{"error": err.Error()})
	default:
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
	}
}

func writeJSON(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(value)
}
