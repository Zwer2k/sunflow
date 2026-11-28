package main

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const testSKI = "0123456789abcdef0123456789abcdef01234567"

// fakeBackend stands in for the wallbox side.
type fakeBackend struct {
	trusted   []string
	addresses []Address
	limits    []float64
	release   int
	device    *Device
	err       error
}

func (f *fakeBackend) Status() Status { return Status{SKI: "own", Version: "test"} }
func (f *fakeBackend) Trust(ski string, address Address) error {
	f.trusted = append(f.trusted, ski)
	f.addresses = append(f.addresses, address)
	return f.err
}
func (f *fakeBackend) Untrust(string) error { return f.err }
func (f *fakeBackend) Device(string) (Device, bool) {
	if f.device == nil {
		return Device{}, false
	}
	return *f.device, true
}
func (f *fakeBackend) SetLimit(_ string, a float64) error {
	f.limits = append(f.limits, a)
	return f.err
}
func (f *fakeBackend) ReleaseLimit(string) error {
	f.release++
	return f.err
}

const testToken = "secret-token"

func request(t *testing.T, b Backend, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	recorder := httptest.NewRecorder()
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer "+testToken)
	NewHandler(b, testToken).ServeHTTP(recorder, r)
	return recorder
}

func TestEveryRequestNeedsTheToken(t *testing.T) {
	// Another App on the Supervisor's network must not steer the wallbox
	cases := []struct {
		name, configured, sent string
		code                   int
	}{
		{"right", testToken, "Bearer " + testToken, http.StatusOK},
		{"wrong", testToken, "Bearer guess", http.StatusUnauthorized},
		{"missing", testToken, "", http.StatusUnauthorized},
		{"none configured", "", "Bearer ", http.StatusUnauthorized},
	}
	for _, c := range cases {
		recorder := httptest.NewRecorder()
		r := httptest.NewRequest(http.MethodGet, "/status", nil)
		r.Header.Set("Authorization", c.sent)
		NewHandler(&fakeBackend{}, c.configured).ServeHTTP(recorder, r)
		if recorder.Code != c.code {
			t.Errorf("%s: got %d, want %d", c.name, recorder.Code, c.code)
		}
	}
}

func TestTrustNormalizesTheSKI(t *testing.T) {
	// Devices show the SKI in groups, upper case
	b := &fakeBackend{}
	r := request(t, b, http.MethodPost, "/trust/0123-4567-89AB-CDEF-0123-4567-89AB-CDEF-0123-4567", "")
	if r.Code != http.StatusOK || len(b.trusted) != 1 || b.trusted[0] != testSKI {
		t.Fatalf("got %d %v", r.Code, b.trusted)
	}
}

func TestLimitNeedsACurrent(t *testing.T) {
	cases := map[string]int{`{"current_a": 10}`: http.StatusOK, `{}`: http.StatusBadRequest, `{"current_a": -1}`: http.StatusBadRequest, `x`: http.StatusBadRequest}
	for body, code := range cases {
		b := &fakeBackend{}
		if r := request(t, b, http.MethodPost, "/devices/"+testSKI+"/limit", body); r.Code != code {
			t.Errorf("%s: got %d, want %d", body, r.Code, code)
		}
	}
}

func TestReleaseAndErrors(t *testing.T) {
	b := &fakeBackend{}
	if r := request(t, b, http.MethodDelete, "/devices/"+testSKI+"/limit", ""); r.Code != http.StatusOK || b.release != 1 {
		t.Fatalf("release: %d %d", r.Code, b.release)
	}
	// No car plugged in: Sunflow sees a 404, a failed write a 502
	if r := request(t, &fakeBackend{err: errUnknownDevice}, http.MethodPost, "/devices/"+testSKI+"/limit", `{"current_a": 6}`); r.Code != http.StatusNotFound {
		t.Errorf("unknown: %d", r.Code)
	}
	if r := request(t, &fakeBackend{err: errors.New("rejected")}, http.MethodPost, "/devices/"+testSKI+"/limit", `{"current_a": 6}`); r.Code != http.StatusBadGateway {
		t.Errorf("rejected: %d", r.Code)
	}
}

func TestDeviceIsReadAsJSON(t *testing.T) {
	limit := 10.0
	b := &fakeBackend{device: &Device{Connected: true, EVConnected: true, PowerW: 6900, LimitA: &limit}}
	r := request(t, b, http.MethodGet, "/devices/"+testSKI, "")
	if r.Code != http.StatusOK || !strings.Contains(r.Body.String(), `"power_w":6900`) || !strings.Contains(r.Body.String(), `"limit_a":10`) {
		t.Fatalf("got %d %s", r.Code, r.Body.String())
	}
	if r := request(t, &fakeBackend{}, http.MethodGet, "/devices/"+testSKI, ""); r.Code != http.StatusNotFound {
		t.Errorf("untrusted: %d", r.Code)
	}
}

func TestTrustWithTheAddressOfAnotherSegment(t *testing.T) {
	b := &fakeBackend{}
	if r := request(t, b, http.MethodPost, "/trust/"+testSKI, `{"ip": "192.168.20.15", "port": 4711}`); r.Code != http.StatusOK {
		t.Fatalf("got %d", r.Code)
	}
	if r := request(t, b, http.MethodPost, "/trust/"+testSKI, `{"port": "x"}`); r.Code != http.StatusBadRequest {
		t.Errorf("bad body: %d", r.Code)
	}
	if len(b.addresses) != 1 || b.addresses[0] != (Address{IP: "192.168.20.15", Port: 4711}) {
		t.Fatalf("got %v", b.addresses)
	}
}
