/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'base': {
          900: '#0a0e17',
          800: '#0d1220',
          700: '#111827',
          600: '#151c2c',
          500: '#1a2236',
          400: '#1e2940',
          300: '#243049',
        },
        'surface': {
          DEFAULT: '#111827',
          light: '#151c2c',
          border: '#1e2940',
        },
        'accent': {
          cyan: '#00d4ff',
          'cyan-dim': '#0098b8',
          'cyan-glow': '#00d4ff33',
          amber: '#d4a000',
          'amber-dim': '#b8860b',
          red: '#ff3333',
          'red-dim': '#cc2222',
        },
        'text': {
          primary: '#c8cdd5',
          secondary: '#7a8494',
          dim: '#4a5568',
          bright: '#e2e8f0',
        },
      },
      fontFamily: {
        mono: ['"JetBrains Mono"', '"IBM Plex Mono"', 'monospace'],
        display: ['"Barlow"', '"Rajdhani"', 'system-ui', 'sans-serif'],
        sans: ['"Barlow"', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        'xxs': '0.625rem',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'scan': 'scan 8s linear infinite',
        'glow': 'glow 2s ease-in-out infinite alternate',
      },
      keyframes: {
        scan: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100%)' },
        },
        glow: {
          '0%': { opacity: '0.4' },
          '100%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
