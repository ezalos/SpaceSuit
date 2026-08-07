
# File operations
import os
import shutil

# Allow to name computer
import socket
import re
from getpass import getuser

# DB
import json

# Utils
import argparse
from datetime import datetime

# Usage:
#	Add one .file to setup
#	Replace .files by symlink to setup
#	Backup current .files
#	Maybe: working on multiple setups w/ != paths

# Datastructure:
#	Json: ['saved_name','original_place', 'computer']
#	TODO:	Should be changed to dic {.file_name: {infos}}
#	The first level of indexation should be the program itself,
#	Then the couples computer/location 

def get_computer_name():
	identifier = socket.gethostname() + "." + getuser()
	identifier = re.sub(r"[^A-Za-z0-9\.]", ".", identifier)
	if identifier[-1] == ".":
		identifier = identifier + 'x'
	return identifier


DOTFILE_DIR = 'dotfiles/'
BACKUP_DIR = DOTFILE_DIR + 'old/'
JSON_FILE = DOTFILE_DIR + 'depedencies.json'
IDENTIFIER = get_computer_name()
JSON_OBJ = []
PWD = os.path.dirname(os.path.realpath(__file__))
if PWD[-len('scripts'):] == 'scripts':
	PWD = PWD[:-len('scripts')]
print(f'Current pwd: {PWD}')

with open(JSON_FILE) as json_file:
	JSON_OBJ = json.load(json_file)

def save_json():
	with open(JSON_FILE, 'w') as outfile:
		json.dump(JSON_OBJ, outfile, indent=4)

def get_dotfile_name(src):
	if '/' in src:
		return src.split('/')[-1]
	return src

def create_backup(src, dst):
	now = datetime.now()
	current_time = now.strftime("%Y-%m-%d_%H:%M")
	if os.path.exists(src):
		shutil.copy(src, BACKUP_DIR + dst + '_' + IDENTIFIER + '_' + current_time)
		print(f'Backed up as {DOTFILE_DIR + dst}')
	else:
		print(f'{src} does not exist, no backup will be done')


def add_file(src, dst=None, force_dst_update=False, keep_src=False):
	if dst == None:
		dst = get_dotfile_name(src)
	print(f'Adding {dst} from {src}')
	if os.path.islink(src):
		print(f'Error: {src} is already a symlink')
		return

	# Collect data
	data = [dst, src, IDENTIFIER]
	if data not in JSON_OBJ:
		JSON_OBJ.append(data)
	# Backup
	create_backup(src, dst)
	# Copy
	if force_dst_update or not os.path.exists(DOTFILE_DIR + dst):
		if os.path.exists(src):
			shutil.copy(src, DOTFILE_DIR + dst)
			print(f'{DOTFILE_DIR + dst} as been added as main {src}')
	else:
		print(f'File {dst} already exist in Setup')
	# Replace
	if not keep_src:
		if os.path.exists(src):
			os.remove(src)
		dirs = os.path.dirname(src)
		print(f'Extarcting dir part of src: {dirs}')
		if not os.path.exists(dirs):
			print(f'{dirs} does not exist: creating it')
			os.makedirs(dirs)
		os.symlink(PWD + DOTFILE_DIR + dst, src)
	# Save depedencie
	save_json()



if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument('-a', '--add', nargs='+', help='-a SRC [.filename]	Add one file to setup, create backup, make symlink')
	parser.add_argument('-f', '--force', default=False, action='store_true', help='Force file remplacement')
	parser.add_argument('-k', '--keep', default=False, action='store_true', help='Will not alter source file')
	args = parser.parse_args()
	if args.add:
		src = args.add[0]
		dst = args.add[1] if len(args.add) >= 2 else None
		add_file(src, dst, args.force, args.keep)
