-- Basic vim options (works on any nvim version)

local opt = vim.opt

-- Line numbers and display
opt.number = true
opt.cursorline = true
opt.ruler = true
opt.signcolumn = "yes"
opt.colorcolumn = "81"

-- Syntax and colors
vim.cmd("syntax on")
opt.termguicolors = true
opt.background = "dark"

-- Mouse
opt.mouse = "a"

-- Tabs and indentation (defaults)
opt.tabstop = 4
opt.shiftwidth = 4
opt.softtabstop = 4
opt.expandtab = true
opt.autoindent = true
opt.smartindent = true

-- File type specific indentation
vim.api.nvim_create_autocmd("FileType", {
  pattern = { "javascript", "html", "css", "lua", "json", "yaml" },
  callback = function()
    vim.opt_local.tabstop = 2
    vim.opt_local.shiftwidth = 2
    vim.opt_local.softtabstop = 2
  end,
})

vim.api.nvim_create_autocmd("FileType", {
  pattern = { "python" },
  callback = function()
    vim.opt_local.tabstop = 4
    vim.opt_local.shiftwidth = 4
    vim.opt_local.softtabstop = 4
    vim.opt_local.expandtab = true
    vim.opt_local.textwidth = 79
  end,
})

-- Splits
opt.splitbelow = true
opt.splitright = true

-- Search
opt.hlsearch = true
opt.incsearch = true
opt.ignorecase = true
opt.smartcase = true

-- Encoding
opt.encoding = "utf-8"
opt.fileencoding = "utf-8"

-- Folding
opt.foldmethod = "indent"
opt.foldlevel = 99

-- Misc
opt.wrap = false
opt.scrolloff = 8
opt.updatetime = 250
opt.undofile = true

-- Clipboard (system clipboard via Ctrl+C in visual mode)
vim.keymap.set("v", "<C-c>", '"+y', { desc = "Copy to system clipboard" })

-- Disable netrw (nvim-tree replaces it when available)
vim.g.loaded_netrw = 1
vim.g.loaded_netrwPlugin = 1

-- Leader key
vim.g.mapleader = " "
vim.g.maplocalleader = " "
