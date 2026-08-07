syntax on
set nu
set tabstop=4
set shiftwidth=4
set colorcolumn=81
set autoindent
set smartindent
" set relativenumber
set mouse=a
set noexpandtab

"Remap for copy to real clipboard
vnoremap <C-c> "+y

"Tabulations specialised for file type
au BufNewFile, BufRead *.py
    \ set tabstop=4
    \ softtabstop=4
    \ shiftwidth=4
    \ textwidth=79
    \ expandtab
    \ autoindent
    \ fileformat=unix

au BufNewFile, BufRead *.js, *.html, *.css
    \ set tabstop=2
    \ softtabstop=2
    \ shiftwidth=2

"Flag unnecessery whitespaces
au BufRead, BufNewFile *.py,*.pyw,*.c,*.h match BadWhitespace /\s\+$/

set encoding=utf-8

" Setup vim-plug
if empty(glob('~/.vim/autoload/plug.vim'))
  silent !curl -fLo ~/.vim/autoload/plug.vim --create-dirs
    \ https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
  autocmd VimEnter * PlugInstall --sync | source $MYVIMRC
endif

" Skip initialization for vim-tiny or vim-small
if !1 | finish | endif

if has('vim_starting')
  set nocompatible               " Be iMproved
  call plug#begin('~/.vim/plugged')
endif

"Live compilations errors for c and other languages
Plug 'vim-syntastic/syntastic'

"a universal set of defaults that everyone can agree on.
Plug 'tpope/vim-sensible'

"File management
Plug 'scrooloose/nerdtree'
Plug 'jistr/vim-nerdtree-tabs'
Plug 'ctrlpvim/ctrlp.vim'

"Virtual env compability with syntastic
Plug 'jmcantrell/vim-virtualenv'

"Improved C syntax color
Plug 'justinmk/vim-syntax-extra'
"Header for 42
Plug 'pbondoer/vim-42header'

"Python intellisense engine autocompletion
"Plug 'neoclide/coc.nvim', {'branch': 'release'}

"VIM neo-completion and remap of navigation
"Plug 'deoplete-plugins/deoplete-jedi'

if has('nvim')
"  Plug 'Shougo/deoplete.nvim', { 'do': ':UpdateRemotePlugins' }
else
"  Plug 'Shougo/deoplete.nvim'
"  Plug 'roxma/nvim-yarp'
"  Plug 'roxma/vim-hug-neovim-rpc'
endif

let g:deoplete#enable_at_startup = 1
inoremap <expr> <C-j> pumvisible() ? "\<C-n>" : "\<C-j>"
inoremap <expr> <C-k> pumvisible() ? "\<C-p>" : "\<C-k>"

"Surround.vim is all about surroundings: parentheses, brackets, quotes,..
Plug 'tpope/vim-surround'

"Vim motions on speed! 
Plug 'easymotion/vim-easymotion'
map <Leader> <Plug>(easymotion-prefix)

"lean & mean status/tabline for vim that's light as air
Plug 'bling/vim-airline'
Plug 'vim-airline/vim-airline-themes'

"Vim theme, really dope
Plug 'sonph/onehalf', {'rtp': 'vim/'}

"provides an overview of the structure of source code files 
"Plug 'vim-scripts/taglist.vim'

"Rainbow Parentheses
Plug 'frazrepo/vim-rainbow'

"Allow to comment selected text
Plug 'preservim/nerdcommenter'

"Insert or delete brackets, parens, quotes in pair.
Plug 'jiangmiao/auto-pairs'

"Run your favorite search tool from Vim, with an enhanced results list.
Plug 'mileszs/ack.vim'

"Multiple cursor support
Plug 'terryma/vim-multiple-cursors'

"Show git modifs next to line numbers
Plug 'airblade/vim-gitgutter'

"Can use :Git commands in vim
Plug 'tpope/vim-fugitive'

"Enable folding
set foldmethod=indent
set foldlevel=99
"Enable folding with the spacebar
nnoremap <space> za

"Python folding done right
Plug 'tmhedberg/SimpylFold'

"Python auto-indent
Plug 'vim-scripts/indentpython.vim'

"Python good autocomplete
"Plug 'Valloric/YouCompleteMe'
"Auto-complete window go away
"let g:ycm_autoclose_preview_window_after_completion=1
"Space - g will go to definition
"map <leader>g  :YcmCompleter GoToDefinitionElseDeclaration<CR>

"check your syntax on each save 
"Plug 'vim-syntastic/syntastic'  " Already defined above

"PEP 8 checking 
Plug 'nvie/vim-flake8'

let python_highlight_all=1

"Seems cool, need to better understand
"Plug 'junegunn/fzf', { 'do': { -> fzf#install() } }
"Plug 'junegunn/fzf.vim'

"python with virtualenv support
"py << EOF
"import os
"import sys
"if 'VIRTUAL_ENV' in os.environ:
"  project_base_dir = os.environ['VIRTUAL_ENV']
"  activate_this = os.path.join(project_base_dir, 'bin/activate_this.py')
"  execfile(activate_this, dict(__file__=activate_this))
"EOF

call plug#end()

" Syntastic configuration (after plugins are loaded)
" Only configure statusline if syntastic is available
function! SetupSyntasticStatusline()
  if exists('g:loaded_syntastic_plugin')
    set statusline+=%#warningmsg#
    set statusline+=%{SyntasticStatuslineFlag()}
    set statusline+=%*
  endif
endfunction

" Call the setup function after VimEnter to ensure plugins are loaded
autocmd VimEnter * call SetupSyntasticStatusline()

let g:syntastic_always_populate_loc_list = 1
let g:syntastic_auto_loc_list = 1
let g:syntastic_check_on_open = 0
let g:syntastic_check_on_wq = 0
let g:syntastic_c_include_dirs = [ '../includes', 'includes', 'libft/includes' ]
"Remove Pylint checker
let g:syntastic_python_checkers = ['python']
"let g:syntastic_loc_list_height = 5

" Syntastic responsive size
" see :h syntastic-loclist-callback
function! SyntasticCheckHook(errors)
	if !empty(a:errors)
		let g:syntastic_loc_list_height = min([len(a:errors), 10])
	endif
endfunction

set t_Co=256
set cursorline
" colorscheme onehalfdark  " Uncomment after running :PlugInstall

let g:rainbow_active = 1
let g:airline_theme='onehalfdark'
let g:airline_extensions = []
" lightline
" let g:lightline.colorscheme='onehalfdark'
if exists('+termguicolors')
  let &t_8f = "\<Esc>[38;2;%lu;%lu;%lum"
  let &t_8b = "\<Esc>[48;2;%lu;%lu;%lum"
  set termguicolors
endif

" Open new split panes to right and bottom, which feels more natural
set splitbelow
set splitright

" Quicker window movement
nnoremap <C-j> <C-w>j
nnoremap <C-k> <C-w>k
nnoremap <C-h> <C-w>h
nnoremap <C-l> <C-w>l

" ========================================
" SETUP INSTRUCTIONS:
" ========================================
" 1. Start Vim normally - it should now work without errors
" 2. Run :PlugInstall to install all plugins
" 3. After installation completes, uncomment the colorscheme line above
" 4. Restart Vim to see the full theme and all features
" ========================================










