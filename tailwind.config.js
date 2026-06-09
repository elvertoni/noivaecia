/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './**/templates/**/*.html',
    './static/src/**/*.css',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        app: {
          bg: '#F6F7F9',
          surface: '#FFFFFF',
          panel: '#F8FAFC',
          ink: '#111827',
          muted: '#475569',
          border: '#CBD5E1',
        },
        brand: {
          50: '#FFF1F3',
          100: '#FFE4E9',
          200: '#FECDD6',
          300: '#FDA4B5',
          400: '#FB7189',
          500: '#E11D48',
          600: '#BE123C',
          700: '#9F1239',
          800: '#881337',
          900: '#4C0519',
        },
      },
    },
  },
  plugins: [],
};
