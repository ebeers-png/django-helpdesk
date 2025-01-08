from django.db.models import Q
from helpdesk.models import CustomField, KBItem, Queue, FormType
from helpdesk.lib import get_beam_state, map_form_fields_to_state_data
from seed.models import PropertyView, TaxLotView


class AbstractCreateTicketMixin():
    def get_initial(self):
        initial_data = {}
        request = self.request
        try:
            initial_data['queue'] = Queue.objects.get(slug=request.GET.get('queue', None)).id
        except Queue.DoesNotExist:
            pass
        u = request.user
        if u.is_authenticated and u.usersettings_helpdesk.use_email_as_submitter and u.email:
            initial_data['submitter_email'] = u.email

        query_param_fields = ['submitter_email', 'title', 'description', 'queue', 'kbitem']
        custom_fields = ["e_%s" % f.field_name for f in CustomField.objects.filter(staff=True,
                                                                                   ticket_form=self.form_id)]
        query_param_fields += custom_fields
        
        ## Prepopulate form from ID

        form = FormType.objects.get(pk=self.form_id)
        building_id_field = form.customfield_set.filter(field_name='building_id').exclude(column__isnull=True).select_related('column').first()
        building_id = request.GET.get('e_building_id', None)
        if building_id and form.prepopulate and form.prepopulation_cycle and building_id_field:
            org_id = form.organization_id
            column = building_id_field.column
            if column.is_extra_data:
                lookup = Q(**{f'state__extra_data__{column.column_name}': building_id})
            else: 
                lookup = Q(**{f'state__{column.column_name}': building_id})
            
            if column.table_name == 'PropertyState':
                view = PropertyView.objects.filter( lookup, cycle_id=form.prepopulation_cycle_id, ).first()
            else: 
                view = TaxLotView.objects.filter( lookup, cycle_id=form.prepopulation_cycle_id, ).first()
            
            if view:
                state = get_beam_state(org_id, view.id, column.table_name)
                beam_data = map_form_fields_to_state_data(state, self.form_id, column.table_name)
                initial_data.update(e_prepopulated="Yes", **beam_data) 
        
        for qpf in query_param_fields:
            # Form only accepts qpf without the e_
            qpf_name = qpf[2:] if qpf[0:2] == 'e_' else qpf
            initial_data[qpf_name] = request.GET.get(qpf, initial_data.get(qpf_name, ""))

        initial_data = {k: ('' if v == 'null' else v) for k, v in initial_data.items()}
        return initial_data

    def get_form_kwargs(self, *args, **kwargs):
        kwargs = super().get_form_kwargs(*args, **kwargs)
        kbitem = self.request.GET.get(
            'kbitem',
            self.request.POST.get('kbitem', None),
        )
        if kbitem:
            try:
                kwargs['kbcategory'] = KBItem.objects.get(pk=int(kbitem)).category
            except (ValueError, KBItem.DoesNotExist):
                pass
        return kwargs
        
        