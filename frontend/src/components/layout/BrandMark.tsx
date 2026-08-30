import { Box, Typography } from '@mui/material';

/** The chevron-and-dot mark, matching public/favicon.svg. */
export function BrandGlyph({ size = 22 }: { size?: number }) {
  return (
    <Box
      component="svg"
      viewBox="0 0 32 32"
      sx={{ width: size, height: size, flexShrink: 0, display: 'block' }}
      aria-hidden
    >
      <path
        d="M6 20.5 L11.5 15 L15.5 19 L21 11.5"
        fill="none"
        stroke="currentColor"
        strokeWidth={2.4}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="24.5" cy="9.5" r="2.8" fill="currentColor" />
    </Box>
  );
}

export default function BrandMark({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, color: 'primary.main', minWidth: 0 }}>
      <BrandGlyph />
      {!collapsed && (
        <Typography
          variant="overline"
          sx={{
            color: 'text.primary',
            fontWeight: 700,
            fontSize: '0.75rem',
            letterSpacing: '0.14em',
            whiteSpace: 'nowrap',
          }}
        >
          Quant<Box component="span" sx={{ color: 'primary.main' }}>Terminal</Box>
        </Typography>
      )}
    </Box>
  );
}
