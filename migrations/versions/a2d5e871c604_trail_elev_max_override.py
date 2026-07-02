"""trail elev_max_override

Corectie manuala a altitudinii maxime afisate (GPS-ul subestimeaza cota
varfurilor). NULL = se afiseaza valoarea calculata din GPX, ca pana acum.

Revision ID: a2d5e871c604
Revises: f3a91c40d817
Create Date: 2026-07-02 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2d5e871c604'
down_revision = 'f3a91c40d817'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('trail', schema=None) as batch_op:
        batch_op.add_column(sa.Column('elev_max_override', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('trail', schema=None) as batch_op:
        batch_op.drop_column('elev_max_override')
