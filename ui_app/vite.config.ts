import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { defineConfig } from 'vite';

export default defineConfig(() => {
  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, '.'),
      },
    },
    build: {
      // Increase chunk size warning limit to 1MB
      chunkSizeWarningLimit: 1000,
      // Enable minification with terser for better compression
      minify: 'terser',
      terserOptions: {
        compress: {
          drop_console: true,
          drop_debugger: true,
          pure_funcs: ['console.log', 'console.info', 'console.debug'],
        },
        mangle: {
          safari10: true,
        },
      },
      // Code splitting with manual chunks
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (id.includes('node_modules')) {
              // Group React and its tightly-coupled ecosystem into vendor-framework to prevent circular references
              if (
                id.includes('react') ||
                id.includes('react-dom') ||
                id.includes('scheduler') ||
                id.includes('motion') ||
                id.includes('framer-motion') ||
                id.includes('@paper-design') ||
                id.includes('@yogesharc')
              ) {
                return 'vendor-framework';
              }
              if (id.includes('lucide-react') || id.includes('@hugeicons')) {
                return 'vendor-icons';
              }
              return 'vendor-deps';
            }
          },
          // Optimize chunk naming
          chunkFileNames: 'assets/[name]-[hash].js',
          entryFileNames: 'assets/[name]-[hash].js',
          assetFileNames: 'assets/[name]-[hash].[ext]',
        },
      },
      // Enable CSS code splitting
      cssCodeSplit: true,
      // Generate source maps for production debugging
      sourcemap: true,
      // Optimize dependencies
      commonjsOptions: {
        include: [/node_modules/],
      },
    },
    server: {
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:5000',
          changeOrigin: true,
        },
        '/events': {
          target: 'http://127.0.0.1:5000',
          changeOrigin: true,
        },
        '/overlay': {
          target: 'http://127.0.0.1:5000',
          changeOrigin: true,
        },
      },
      // HMR is disabled in AI Studio via DISABLE_HMR env var.
      // Do not modify—file watching is disabled to prevent flickering during agent edits.
      hmr: process.env.DISABLE_HMR !== 'true',
      // Disable file watching when DISABLE_HMR is true to save CPU during agent edits.
      watch: process.env.DISABLE_HMR === 'true' ? null : {},
    },
  };
});
