
APT_ESSENTIALS =	git\
					vim\
					make

DOT_FILES_DIR =		dotfiles/

DOT_FILES =			$(wildcard $(DOT_FILES_DIR).*)

DOT_REM =			$(DOT_FILES_DIR)..\
					$(DOT_FILES_DIR).

OTHERVAR = 			$(filter-out $(DOT_REM),$(DOT_FILES))

DOT =				$(DOT_FILES:$(DOT_FILES_DIR)%=$(HOME)/%)


make: $(DOT_FILES) $(DOT)
	echo Hello
	echo $(HOME)
	echo $(DOT_FILES)

$(HOME)%: $(DOT_FILES_DIR)%
	echo  left $< right $@
	diff $< $@

e:
	echo $(OTHERVAR)

apt:
	sudo apt update -y
	sudo apt upgrade -y
	sudo apt install -y $(APT_ESSENTIALS)
	sudo apt autoremove -y
