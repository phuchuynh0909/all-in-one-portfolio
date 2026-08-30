import { useEffect, useState } from 'react';
import { Box, Typography } from '@mui/material';

/** Terminal-style UTC clock. Purely ambient, but it is what a desk expects. */
export default function DeskClock() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const time = now.toISOString().slice(11, 19);

  return (
    <Box sx={{ display: { xs: 'none', md: 'flex' }, alignItems: 'baseline', gap: 0.75 }}>
      <Typography variant="mono" sx={{ color: 'text.primary', fontSize: '0.8125rem' }}>
        {time}
      </Typography>
      <Typography variant="overline2" sx={{ fontSize: '0.5625rem' }}>
        UTC
      </Typography>
    </Box>
  );
}
