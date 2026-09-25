package domain

import "context"

type includeBSEKey struct{}

// WithBSE explicitly includes Beijing in a batch operation. The default is Shanghai/Shenzhen.
func WithBSE(ctx context.Context) context.Context {
	return context.WithValue(ctx, includeBSEKey{}, true)
}

func IncludesBSE(ctx context.Context) bool {
	v, _ := ctx.Value(includeBSEKey{}).(bool)
	return v
}
