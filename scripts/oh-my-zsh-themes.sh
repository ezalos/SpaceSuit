#!/bin/bash
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
	# Linux
	sed -i 's~ZSH_THEME=\"robbyrussell~ZSH_THEME=\"agnoster~g' ~/.zshrc
	# clone
	git clone https://github.com/powerline/fonts.git --depth=1
	# install
	cd fonts
	./install.sh
	# clean-up a bit
	cd ..
	rm -rf fonts
elif [[ "$OSTYPE" == "darwin"* ]]; then
	# Mac OSX
	sed -i '' 's~ZSH_THEME=\"robbyrussell~ZSH_THEME=\"agnoster~g' ~/.zshrc
fi
source ~/.zshrc
