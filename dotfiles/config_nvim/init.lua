-- Load basic options that work on any nvim version
require("options")

-- Check nvim version - lazy.nvim requires 0.8+
if vim.fn.has("nvim-0.8") == 0 then
  -- Fallback: just use basic options, no plugins
  return
end

-- Bootstrap lazy.nvim (self-installing)
local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if vim.fn.isdirectory(lazypath) == 0 then
  vim.fn.system({
    "git", "clone", "--filter=blob:none",
    "https://github.com/folke/lazy.nvim.git",
    "--branch=stable", lazypath,
  })
end
vim.opt.rtp:prepend(lazypath)

-- Load plugins (wrapped in pcall for safety)
local ok, lazy = pcall(require, "lazy")
if ok then
  lazy.setup(require("plugins"))
end

-- Load keymaps
require("keymaps")
