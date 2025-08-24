import { Container, Grid, Paper } from '@mui/material';
import SectorList from '../components/sector/SectorList';
import SectorChart from '../components/sector/SectorChart';

export default function Sector() {
  return (
    <Container maxWidth="xl">
      <Grid container spacing={3}>
        <Grid item xs={12}>
          <Paper sx={{ p: 3 }}>
            <SectorChart />
          </Paper>
        </Grid>
        <Grid item xs={12}>
          <Paper sx={{ p: 3 }}>
            <SectorList />
          </Paper>
        </Grid>
      </Grid>
    </Container>
  );
}
