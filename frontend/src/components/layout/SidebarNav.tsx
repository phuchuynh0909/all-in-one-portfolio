import { Link, useLocation } from 'react-router-dom';
import {
  Box,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Tooltip,
  Typography,
} from '@mui/material';
import { navGroups } from './navigation';

interface SidebarNavProps {
  collapsed: boolean;
  /** Called after navigating — used to close the temporary drawer on mobile. */
  onNavigate?: () => void;
}

export default function SidebarNav({ collapsed, onNavigate }: SidebarNavProps) {
  const { pathname } = useLocation();

  const isActive = (path: string) =>
    path === '/' ? pathname === '/' : pathname === path || pathname.startsWith(`${path}/`);

  return (
    <Box
      component="nav"
      aria-label="Main navigation"
      sx={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', px: collapsed ? 0.75 : 1.25, py: 1.5 }}
    >
      {navGroups.map((group, groupIndex) => (
        <Box key={group.id} sx={{ mb: 2 }}>
          {collapsed ? (
            groupIndex > 0 && (
              <Box sx={{ height: '1px', bgcolor: 'line.subtle', mx: 1, mb: 1.5 }} />
            )
          ) : (
            <Typography
              variant="overline2"
              sx={{ px: 1.25, mb: 0.5, color: 'text.tertiary', fontSize: '0.625rem' }}
            >
              {group.label}
            </Typography>
          )}

          <List disablePadding sx={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {group.items.map((item) => {
              const active = isActive(item.path);
              return (
                <ListItem key={item.path} disablePadding>
                  <Tooltip
                    title={collapsed ? item.label : ''}
                    placement="right"
                    disableHoverListener={!collapsed}
                  >
                    <ListItemButton
                      component={Link}
                      to={item.path}
                      selected={active}
                      onClick={onNavigate}
                      sx={{
                        minHeight: 32,
                        px: collapsed ? 0 : 1.25,
                        justifyContent: collapsed ? 'center' : 'flex-start',
                        position: 'relative',
                        color: active ? 'primary.main' : 'text.secondary',
                        // Left accent bar marks the active route.
                        '&::before': active
                          ? {
                              content: '""',
                              position: 'absolute',
                              left: 0,
                              top: 6,
                              bottom: 6,
                              width: 2,
                              borderRadius: 1,
                              bgcolor: 'primary.main',
                            }
                          : undefined,
                      }}
                    >
                      <ListItemIcon sx={{ minWidth: collapsed ? 0 : 28, color: 'inherit' }}>
                        {item.icon}
                      </ListItemIcon>
                      {!collapsed && (
                        <ListItemText
                          primary={item.label}
                          primaryTypographyProps={{
                            fontSize: '0.8125rem',
                            fontWeight: active ? 600 : 500,
                            noWrap: true,
                          }}
                        />
                      )}
                    </ListItemButton>
                  </Tooltip>
                </ListItem>
              );
            })}
          </List>
        </Box>
      ))}
    </Box>
  );
}
