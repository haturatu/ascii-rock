.PHONY: all build install uninstall clean

all: build

build:
	python3 -m build

install:
	pip install . --force-reinstall
	@echo "Done! To use, run \`ascii-rock -h\`"

uninstall:
	pip uninstall ascii-rock -y

clean:
	rm -rf build dist *.egg-info

