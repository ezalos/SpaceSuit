-- Key mappings

local map = vim.keymap.set

-- Window navigation (Ctrl+hjkl)
map("n", "<C-h>", "<C-w>h", { desc = "Move to left window" })
map("n", "<C-j>", "<C-w>j", { desc = "Move to below window" })
map("n", "<C-k>", "<C-w>k", { desc = "Move to above window" })
map("n", "<C-l>", "<C-w>l", { desc = "Move to right window" })

-- Folding with spacebar
map("n", "<leader><space>", "za", { desc = "Toggle fold" })

-- Nvim-tree
map("n", "<leader>e", ":NvimTreeToggle<CR>", { silent = true, desc = "Toggle file explorer" })

-- Telescope
local tel_ok, builtin = pcall(require, "telescope.builtin")
if tel_ok then
  map("n", "<leader>ff", builtin.find_files, { desc = "Find files" })
  map("n", "<leader>fg", builtin.live_grep, { desc = "Live grep" })
  map("n", "<leader>fb", builtin.buffers, { desc = "Find buffers" })
  map("n", "<leader>fh", builtin.help_tags, { desc = "Help tags" })
end

-- LSP keymaps (attached per-buffer when LSP connects)
vim.api.nvim_create_autocmd("LspAttach", {
  group = vim.api.nvim_create_augroup("UserLspConfig", {}),
  callback = function(ev)
    local opts = { buffer = ev.buf }
    map("n", "gd", vim.lsp.buf.definition, opts)
    map("n", "gr", vim.lsp.buf.references, opts)
    map("n", "K", vim.lsp.buf.hover, opts)
    map("n", "<leader>rn", vim.lsp.buf.rename, opts)
    map("n", "<leader>ca", vim.lsp.buf.code_action, opts)
  end,
})
