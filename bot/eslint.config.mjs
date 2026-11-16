import { fileURLToPath } from 'node:url';
import tseslint from 'typescript-eslint';

export default tseslint.config({
  files: ['src/**/*.ts'],
  extends: tseslint.configs.recommended,
  languageOptions: {
    parserOptions: {
      project: './tsconfig.json',
      tsconfigRootDir: fileURLToPath(new URL('.', import.meta.url)),
    },
  },
  rules: {
    '@typescript-eslint/no-floating-promises': 'error',
  },
});
