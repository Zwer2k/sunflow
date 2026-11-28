// Sunflow EEBUS: lets Sunflow control EEBUS wallboxes. Runs as its own Home Assistant App in
// the host network (EEBUS finds devices by mDNS) and offers Sunflow a small local HTTP API.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// Set at build time (-ldflags "-X main.version=...")
var version = "dev"

// logger passes eebus-go's messages to the App log; trace and debug only when asked for.
type logger struct{ debug bool }

func (l *logger) Trace(...any)          {}
func (l *logger) Tracef(string, ...any) {}
func (l *logger) Debug(args ...any) {
	if l.debug {
		log.Print(append([]any{"DEBUG "}, args...)...)
	}
}
func (l *logger) Debugf(format string, args ...any) {
	if l.debug {
		log.Printf("DEBUG "+format, args...)
	}
}
func (l *logger) Info(args ...any)                  { log.Print(args...) }
func (l *logger) Infof(format string, args ...any)  { log.Printf(format, args...) }
func (l *logger) Error(args ...any)                 { log.Print(append([]any{"ERROR "}, args...)...) }
func (l *logger) Errorf(format string, args ...any) { log.Printf("ERROR "+format, args...) }

func main() {
	dataDir := flag.String("data", "/data", "directory for the identity and the trusted devices")
	port := flag.Int("port", 4712, "EEBUS (SHIP) port")
	listen := flag.String("listen", "172.30.32.1:4713", "address of the API for Sunflow")
	debug := flag.Bool("debug", false, "log EEBUS details")
	token := flag.String("token", "", "token Sunflow sends (Authorization: Bearer)")
	flag.Parse()
	log.SetFlags(log.Ldate | log.Ltime)
	// Options of the Home Assistant App, when run as one
	var options struct {
		Debug bool   `json:"debug"`
		Token string `json:"token"`
	}
	if data, err := os.ReadFile(filepath.Join(*dataDir, "options.json")); err == nil {
		if err := json.Unmarshal(data, &options); err != nil {
			log.Printf("WARNING options.json unreadable: %v", err)
		}
		*debug = *debug || options.Debug
		if *token == "" {
			*token = strings.TrimSpace(options.Token)
		}
	}
	if *token == "" {
		log.Printf("WARNING No token set: the API answers nothing. Enter the token Sunflow shows (Devices → Connect directly → EEBUS) in this App's configuration and restart it")
	}

	backend, err := newBackend(*dataDir, *port, &logger{debug: *debug})
	if err != nil {
		log.Fatalf("ERROR Starting EEBUS failed: %v", err)
	}
	backend.start()
	log.Printf("Sunflow EEBUS %s started: SKI %s, EEBUS port %d, API on %s", version, backend.ski, *port, *listen)

	server := &http.Server{Addr: *listen, Handler: NewHandler(backend, *token), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("ERROR API on %s failed: %v", *listen, err)
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = server.Shutdown(ctx)
	backend.stop()
	fmt.Println("Sunflow EEBUS stopped")
}
