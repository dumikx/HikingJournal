"""photo original_key

Pozele urcate direct in R2 au doua variante: originalul (original_key)
si varianta redimensionata pentru afisare (key). Pozele vechi au doar key.

Revision ID: f3a91c40d817
Revises: 09aa2b8b54cc
Create Date: 2026-07-02 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3a91c40d817'
down_revision = '09aa2b8b54cc'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.add_column(sa.Column('original_key', sa.String(length=300), nullable=True))


def downgrade():
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.drop_column('original_key')
