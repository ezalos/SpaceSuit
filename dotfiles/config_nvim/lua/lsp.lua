-- LSP and completion configuration
-- Only sets up servers that are actually installed on the system

local cmp = require("cmp")
local luasnip = require("luasnip")

-- Completion setup
local capabilities = require("cmp_nvim_lsp").default_capabilities()

cmp.setup({
  snippet = {
    expand = function(args)
      luasnip.lsp_expand(args.body)
    end,
  },
  mapping = cmp.mapping.preset.insert({
    ["<C-p>"] = cmp.mapping.select_prev_item(),
    ["<C-n>"] = cmp.mapping.select_next_item(),
    ["<C-d>"] = cmp.mapping.scroll_docs(-4),
    ["<C-f>"] = cmp.mapping.scroll_docs(4),
    ["<C-Space>"] = cmp.mapping.complete(),
    ["<C-e>"] = cmp.mapping.close(),
    ["<CR>"] = cmp.mapping.confirm({
      behavior = cmp.ConfirmBehavior.Replace,
      select = true,
    }),
    ["<Tab>"] = cmp.mapping(function(fallback)
      if cmp.visible() then
        cmp.select_next_item()
      elseif luasnip.expand_or_jumpable() then
        luasnip.expand_or_jump()
      else
        fallback()
      end
    end, { "i", "s" }),
    ["<S-Tab>"] = cmp.mapping(function(fallback)
      if cmp.visible() then
        cmp.select_prev_item()
      elseif luasnip.jumpable(-1) then
        luasnip.jump(-1)
      else
        fallback()
      end
    end, { "i", "s" }),
  }),
  sources = {
    { name = "nvim_lsp" },
    { name = "luasnip" },
    { name = "buffer" },
    { name = "path" },
  },
})

-- Conditionally configure LSP servers (only if the binary exists)
local servers = {
  pylsp = { executable = "pylsp" },
  clangd = { executable = "clangd" },
  ts_ls = { executable = "typescript-language-server" },
  lua_ls = {
    executable = "lua-language-server",
    settings = {
      Lua = {
        diagnostics = { globals = { "vim" } },
      },
    },
  },
}

-- Apply capabilities to all LSP servers
vim.lsp.config("*", { capabilities = capabilities })

for server, cfg in pairs(servers) do
  if vim.fn.executable(cfg.executable) == 1 then
    vim.lsp.config(server, {
      settings = cfg.settings or {},
    })
    vim.lsp.enable(server)
  end
end
