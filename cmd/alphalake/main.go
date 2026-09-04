package main

import (
	"fmt"
	"os"

	duckstore "github.com/yinhm/alphalake/internal/store/duckdb"
)

const version = "0.0.0-dev"

func usage() {
	fmt.Fprintln(os.Stderr, "usage: alphalake <command>")
	fmt.Fprintln(os.Stderr, "commands: version, schema, status")
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
	case "status":
		fmt.Printf("AlphaLake %s\n", version)
		fmt.Println("store: DuckDB (driver integration pending next slice)")
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
