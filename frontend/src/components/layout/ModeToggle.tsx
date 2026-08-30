import { IconButton, Tooltip } from '@mui/material';
import LightModeOutlinedIcon from '@mui/icons-material/LightModeOutlined';
import DarkModeOutlinedIcon from '@mui/icons-material/DarkModeOutlined';
import { useColorMode } from '../../theme';

export default function ModeToggle() {
  const { mode, toggleMode } = useColorMode();
  const next = mode === 'dark' ? 'light' : 'dark';

  return (
    <Tooltip title={`Switch to ${next} mode`}>
      <IconButton onClick={toggleMode} size="small" aria-label={`Switch to ${next} mode`}>
        {mode === 'dark' ? (
          <LightModeOutlinedIcon fontSize="small" />
        ) : (
          <DarkModeOutlinedIcon fontSize="small" />
        )}
      </IconButton>
    </Tooltip>
  );
}
