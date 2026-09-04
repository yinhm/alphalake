package main

import (
	"context"
	"fmt"
	"os"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const version = "0.0.0-dev"

func usage() {
	fmt.Fprintln(os.Stderr, "usage: alphalake <command> [args]")
	fmt.Fprintln(os.Stderr, "commands:")
	fmt.Fprintln(os.Stderr, "  version")
	fmt.Fprintln(os.Stderr, "  schema")
	fmt.Fprintln(os.Stderr, "  init <db-path>")
	fmt.Fprintln(os.Stderr, "  status")
}

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}

	switch os.Args[1] {
	case "version":
		fmt.Println(version)

	case "schema":
		names, err := duckstore.Names()
		if err != nil {
			fatal(err)
		}
		for _, name := range names {
			fmt.Println(name)
		}

	case "init":
		if len(os.Args) != 3 {
			usage()
			os.Exit(2)
		}
		path := os.Args[2]
		db, err := duckstore.OpenAndMigrate(context.Background(), path)
		if err != nil {
			fatal(err)
		}
		if err := db.Close(); err != nil {
			fatal(err)
		}
		fmt.Printf("initialized DuckDB: %s\n", path)

	case "status":
		fmt.Printf("AlphaLake %s\n", version)
		fmt.Println("store: DuckDB")
		fmt.Println("primary source: TDX")
		fmt.Println("validation source: CNINFO")

	default:
		usage()
		os.Exit(2)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "error:", err)
	os.Exit(1)
}
