import { Link } from 'react-router-dom';
import { Box, Button, Typography } from '@mui/material';

export default function NotFound() {
  return (
    <Box
      sx={{
        flex: 1,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 1,
        p: 4,
        textAlign: 'center',
      }}
    >
      <Typography variant="mono" sx={{ fontSize: '2.25rem', color: 'primary.main' }}>
        404
      </Typography>
      <Typography variant="h5">No such route</Typography>
      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
        That page is not part of the terminal.
      </Typography>
      <Button component={Link} to="/" variant="outlined" size="small" sx={{ mt: 1.5 }}>
        Back to dashboard
      </Button>
    </Box>
  );
}
