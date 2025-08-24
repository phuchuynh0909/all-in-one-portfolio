import { Container, Typography, Paper, Box } from '@mui/material';

export default function Home() {
  return (
    <Container maxWidth="xl">
      <Box sx={{ py: 4 }}>
        <Paper sx={{ p: 3 }}>
          <Typography variant="h4" component="h1" gutterBottom>
            Investment Tracker
          </Typography>
          <Typography variant="body1">
            Welcome to the Investment Tracker application. Use the navigation menu to:
          </Typography>
          <ul>
            <li>Manage your portfolio positions and transactions</li>
            <li>Analyze sector performance and trends</li>
            <li>Track investment returns and performance</li>
          </ul>
        </Paper>
      </Box>
    </Container>
  );
}
