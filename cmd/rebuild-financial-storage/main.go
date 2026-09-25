package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	store "github.com/yinhm/alphalake/internal/store/duckdb"
)

func main() {
	source := flag.String("source", "", "existing schema51 database (read-only)")
	output := flag.String("output", "", "new candidate database path")
	root := flag.String("root", "", "immutable archive root")
	audit := flag.String("audit", "", "new evidence directory")
	flag.Parse()
	if *source == "" || *output == "" || *root == "" || *audit == "" {
		flag.Usage()
		os.Exit(2)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	r, e := store.RebuildFinancialStorage(ctx, *source, *output, *root, *audit)
	if e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
	if e = json.NewEncoder(os.Stdout).Encode(r); e != nil {
		panic(e)
	}
}
