import { federation } from '@module-federation/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  build: { target: 'esnext' },
  server: { proxy: { '/api': 'http://localhost:8001' } },
  plugins: [
    react(),
    federation({
      name: 'shell',
      remotes: {
        dashboard: {
          type: 'module',
          name: 'dashboard',
          entry: 'http://localhost:5001/remoteEntry.js',
          entryGlobalName: 'dashboard',
          shareScope: 'default',
        },
        customers: {
          type: 'module',
          name: 'customers',
          entry: 'http://localhost:5002/remoteEntry.js',
          entryGlobalName: 'customers',
          shareScope: 'default',
        },
      },
      shared: {
        react: { singleton: true, requiredVersion: '^19.0.0' },
        'react-dom': { singleton: true, requiredVersion: '^19.0.0' },
      },
    }),
  ],
});
