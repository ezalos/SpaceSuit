-- Plugin specs for lazy.nvim

return {
  -- Theme
  {
    "sonph/onehalf",
    priority = 1000,
    config = function(plugin)
      vim.opt.rtp:append(plugin.dir .. "/vim")
      vim.cmd("colorscheme onehalfdark")
    end,
  },

  -- Status line
  {
    "nvim-lualine/lualine.nvim",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    config = function()
      require("lualine").setup({
        options = { theme = "auto" },
      })
    end,
  },

  -- Comments
  {
    "numToStr/Comment.nvim",
    config = function()
      require("Comment").setup()
    end,
  },

  -- Auto-close brackets
  {
    "windwp/nvim-autopairs",
    event = "InsertEnter",
    config = function()
      require("nvim-autopairs").setup()
    end,
  },

  -- Surround text objects
  {
    "kylechui/nvim-surround",
    event = "VeryLazy",
    config = function()
      require("nvim-surround").setup()
    end,
  },

  -- Git signs in gutter
  {
    "lewis6991/gitsigns.nvim",
    config = function()
      require("gitsigns").setup()
    end,
  },

  -- Git commands
  { "tpope/vim-fugitive" },

  -- File explorer (lazy-loaded)
  {
    "nvim-tree/nvim-tree.lua",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    cmd = { "NvimTreeToggle", "NvimTreeOpen" },
    config = function()
      require("nvim-tree").setup()
    end,
  },

  -- Fuzzy finder (lazy-loaded)
  {
    "nvim-telescope/telescope.nvim",
    dependencies = { "nvim-lua/plenary.nvim" },
    cmd = "Telescope",
    keys = {
      { "<leader>ff", desc = "Find files" },
      { "<leader>fg", desc = "Live grep" },
      { "<leader>fb", desc = "Find buffers" },
      { "<leader>fh", desc = "Help tags" },
    },
  },

  -- Treesitter (lazy-loaded on file open)
  {
    "nvim-treesitter/nvim-treesitter",
    event = "BufRead",
    build = ":TSUpdate",
    config = function()
      -- Try legacy API first, fall back to new API (treesitter >= 1.0)
      local ok, configs = pcall(require, "nvim-treesitter.configs")
      if ok then
        configs.setup({
          ensure_installed = {},
          auto_install = false,
          highlight = { enable = true },
          indent = { enable = true },
        })
      else
        require("nvim-treesitter").setup({
          ensure_installed = {},
          auto_install = false,
          highlight = { enable = true },
          indent = { enable = true },
        })
      end
    end,
  },

  -- LSP (pinned to v2.5.0 to avoid Nvim 0.10 deprecation warning; unpin after upgrading to Nvim 0.11)
  {
    "neovim/nvim-lspconfig",
    tag = "v2.5.0",
    event = "BufRead",
    dependencies = {
      "hrsh7th/nvim-cmp",
      "hrsh7th/cmp-nvim-lsp",
      "hrsh7th/cmp-buffer",
      "hrsh7th/cmp-path",
      "L3MON4D3/LuaSnip",
      "saadparwaiz1/cmp_luasnip",
    },
    config = function()
      require("lsp")
    end,
  },
}
