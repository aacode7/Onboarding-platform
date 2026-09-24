import { federation } from '@module-federation/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  build: { target: 'esnext' },
  plugins: [
    react(),
    federation({
      name: 'subscriptions',
      filename: 'remoteEntry.js',
      exposes: { './App': './src/App.tsx' },
      shared: { react: { singleton: true }, 'react-dom': { singleton: true } },
    }),
  ],
});
