.PHONY: all backend app typecheck cli config install verify clean

# Start the backend server
backend:
	cd backend && uv run python -m nanoclaw.main

# Start app
app: typecheck
	cd cli && NODE_OPTIONS='--enable-source-maps' npx tsx src/app.tsx

# TypeScript type check only
typecheck:
	cd cli && npx tsc --noEmit

# Run the CLI
cli:
	cd cli && npm run chat

# Show CLI config
config:
	cd cli && npx tsx src/index.ts config

# Install all dependencies
install:
	cd backend && uv sync
	cd cli && npm install

# Verify project structure
verify:
	@echo "=== Backend ==="
	cd backend && uv run python -c "import nanoclaw; print('Backend package OK')"
	@echo "=== CLI ==="
	cd cli && npx tsx --version

# Clean build artifacts
clean:
	rm -rf backend/dist backend/*.egg-info backend/.venv
	rm -rf cli/dist cli/node_modules
