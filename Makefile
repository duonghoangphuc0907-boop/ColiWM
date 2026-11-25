SHELL := /bin/sh
PYTHON ?= python3

CONFIG_SRC ?= .config/coliwm/coliconfig.py
DESKTOP_SRC ?= coliwm.desktop

BIN_DIR ?= $(HOME)/.local/bin
CONFIG_DIR ?= $(HOME)/.config/coliwm
XSESSION_DIR ?= /usr/share/xsessions

XEPHYR_DISPLAY ?= :1
XEPHYR_SIZE ?= 1280x800
LOG_FILE ?= $(HOME)/.local/share/coliwm.log

.PHONY: help install install-system uninstall xephyr test-run check nix-build clean

help:
	@echo "ColiWM Makefile targets:"
	@echo "  make install        Install coliconfig.py to $(CONFIG_DIR) and wrapper to $(BIN_DIR)"
	@echo "  make install-system Install coliwm.desktop to $(XSESSION_DIR) (requires sudo)"
	@echo "  make uninstall      Remove installed user config and wrapper"
	@echo "  make xephyr         Launch Xephyr on $(XEPHYR_DISPLAY) and run ColiWM inside it"
	@echo "  make check          Run Python syntax check and basic static checks"
	@echo "  make nix-build      Build package via $(NIX_FILE)"
	@echo "  make clean          Remove Nix ./result symlink and local test artifacts"

install: check
	mkdir -p "$(CONFIG_DIR)" "$(BIN_DIR)" "$(dir $(LOG_FILE))"
	cp "$(CONFIG_SRC)" "$(CONFIG_DIR)/coliconfig.py"
	chmod +x "$(CONFIG_DIR)/coliconfig.py"
	printf '%s\n' \
		'#!/bin/sh' \
		'exec $(PYTHON) "$(CONFIG_DIR)/coliconfig.py" >> "$(LOG_FILE)" 2>&1' \
		> "$(BIN_DIR)/coliwm"
	chmod +x "$(BIN_DIR)/coliwm"
	@echo "Installed ColiWM to $(CONFIG_DIR)/coliconfig.py and $(BIN_DIR)/coliwm"

install-system:
	@test -f "$(DESKTOP_SRC)" || { echo "Missing $(DESKTOP_SRC)"; exit 1; }
	sudo mkdir -p "$(XSESSION_DIR)"
	sudo cp "$(DESKTOP_SRC)" "$(XSESSION_DIR)/coliwm.desktop"
	sudo chmod 644 "$(XSESSION_DIR)/coliwm.desktop"
	@echo "Installed $(XSESSION_DIR)/coliwm.desktop"

uninstall:
	rm -f "$(BIN_DIR)/coliwm" "$(CONFIG_DIR)/coliconfig.py"
	@echo "Removed user ColiWM files"

check:
	@test -f "$(CONFIG_SRC)" || { echo "Missing $(CONFIG_SRC)"; exit 1; }
	$(PYTHON) -m py_compile "$(CONFIG_SRC)"
	@echo "Syntax check passed: $(CONFIG_SRC)"

xephyr: check
	@command -v Xephyr >/dev/null 2>&1 || { echo "Xephyr is not installed"; exit 1; }
	Xephyr "$(XEPHYR_DISPLAY)" -screen "$(XEPHYR_SIZE)" -ac &
	XEPHYR_PID=$$!; \
	sleep 1; \
	DISPLAY="$(XEPHYR_DISPLAY)" $(PYTHON) "$(CONFIG_SRC)"; \
	kill "$$XEPHYR_PID" 2>/dev/null || true

nix-build:
	@command -v nix-build >/dev/null 2>&1 || { echo "nix-build is not installed"; exit 1; }
	nix-build "$(NIX_FILE)"

clean:
	rm -rf result
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
